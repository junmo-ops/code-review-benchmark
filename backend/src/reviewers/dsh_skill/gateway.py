"""One frozen model endpoint, no redirects or retries, no credential in the sandbox."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
import json
import socketserver
import threading
import time
from urllib.parse import urlsplit
import httpx

from runner.storage import save


def response_usage(body):
    usage = None
    for line in body.decode('utf-8', 'replace').splitlines():
        if line.startswith('data:') and line[5:].strip() != '[DONE]':
            try:
                value = json.loads(line[5:])
            except ValueError:
                continue
            if isinstance(value, dict) and value.get('usage'):
                usage = value['usage']
    return usage


def summarize(records):
    known = [r['usage'] for r in records if isinstance(r.get('usage'), dict)]
    summary = {}
    for source, target in [('prompt_tokens', 'input_tokens'), ('completion_tokens', 'output_tokens'),
                           ('total_tokens', 'total_tokens')]:
        values = [u[source] for u in known if type(u.get(source)) is int and u[source] >= 0]
        if values:
            summary[target] = sum(values)
    cached = [_cached_tokens(u) for u in known]
    values = [v for v in cached if type(v) is int and v >= 0]
    if values:
        summary['cache_read_tokens'] = sum(values)
    complete = [u for u in known if _complete_usage(u)]
    return summary, dict(known_requests=len(complete), requests=len(records))


def _complete_usage(usage):
    return all(type(usage.get(k)) is int and usage[k] >= 0
               for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'))


def _cached_tokens(usage):
    details = usage.get('prompt_tokens_details')
    return details.get('cached_tokens') if isinstance(details, dict) else None


class Relay(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        record = dict(index=len(self.server.records) + 1, status='started')
        self.server.records.append(record)
        save(self.server.evidence, self.server.records)
        try:
            self.forward(record)
        except (httpx.HTTPError, OSError, ValueError):
            record['status'] = 'transport_or_contract_error'
            self.send_error(502)
        finally:
            save(self.server.evidence, self.server.records)

    def forward(self, record):
        if self.path != '/v1/chat/completions':
            raise ValueError('模型通道只接受 Chat Completions')
        size = int(self.headers.get('Content-Length', '0'))
        if not 0 < size <= 16 * 1024 * 1024:
            raise ValueError('模型请求体大小无效')
        payload = json.loads(self.rfile.read(size))
        if not isinstance(payload, dict) or payload.get('model') != self.server.model:
            raise ValueError('模型通道不能切换模型')
        self.server.inputs.append(json.loads(self.server.redact(json.dumps(payload, ensure_ascii=False))))
        save(self.server.input_evidence, self.server.inputs)
        remaining = self.server.deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('整例时限已到')
        with httpx.Client(timeout=remaining, follow_redirects=False, trust_env=False) as client:
            with client.stream('POST', self.server.endpoint + '/chat/completions', json=payload,
                               headers={'Authorization': 'Bearer ' + self.server.token}) as reply:
                body = _bounded_response(reply, self.server.deadline)
        record.update(status='responded', http_status=reply.status_code,
                      usage=response_usage(body))
        self.send_response(reply.status_code)
        self.send_header('Content-Type', reply.headers.get('Content-Type', 'application/json'))
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        # Structured, redacted evidence is saved above; avoid raw HTTP stderr logs.
        pass


def _bounded_response(reply, deadline):
    parts, size = [], 0
    for chunk in reply.iter_bytes():
        size += len(chunk)
        if size > 16 * 1024 * 1024 or time.monotonic() >= deadline:
            raise ValueError('模型响应超过整例时限或响应大小上限')
        parts.append(chunk)
    return b''.join(parts)


@contextmanager
def serve(socket, endpoint, token, model, deadline, evidence, redact=lambda text: text):
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None or parsed.query or parsed.fragment:
        raise ValueError('DSH 模型接口地址无效')
    with Relay(str(socket), Handler) as server:
        server.endpoint, server.token, server.model = endpoint.rstrip('/'), token, model
        server.deadline, server.evidence, server.records = deadline, evidence, []
        server.input_evidence = evidence.with_name(evidence.name.removesuffix('.requests.json') + '.model-inputs.json')
        server.inputs, server.redact = [], redact
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.records
        finally:
            server.shutdown()
            thread.join(timeout=2)
