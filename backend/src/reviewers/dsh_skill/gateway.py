"""One frozen model endpoint, no redirects or retries, no credential in the sandbox."""
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
import json
import socketserver
import threading
import time
from urllib.parse import urlsplit
import httpx

from runner.storage import save
from .wire import response_observations


def response_usage(body):
    return response_observations(body, 'text/event-stream')['usage']


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


class ResponseLimitError(ValueError):
    """The bounded upstream response exceeded its byte budget."""


class CaseDeadlineError(ValueError):
    """The shared whole-case deadline expired during a model request."""


def _error_kind(error, deadline):
    if isinstance(error, ResponseLimitError):
        return 'response_too_large'
    if isinstance(error, CaseDeadlineError) or time.monotonic() >= deadline:
        return 'case_deadline_exceeded'
    if isinstance(error, httpx.TimeoutException):
        return 'upstream_timeout'
    return 'transport_or_contract_error'


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.request_started = time.monotonic()
        record = dict(index=len(self.server.records) + 1, status='started',
                      started_at=datetime.now(timezone.utc).isoformat())
        self.server.records.append(record)
        save(self.server.evidence, self.server.records)
        try:
            self.forward(record)
        except (BrokenPipeError, ConnectionResetError):
            record['delivery_status'] = 'client_disconnected'
            if record['status'] != 'responded':
                record['status'] = 'client_disconnected'
        except (httpx.HTTPError, OSError, ValueError) as error:
            record.update(status='transport_or_contract_error',
                          error_kind=_error_kind(error, self.server.deadline))
            self._send_failure(record)
        finally:
            record['elapsed_seconds'] = round(time.monotonic() - self.request_started, 3)
            save(self.server.evidence, self.server.records)

    def _send_failure(self, record):
        try:
            self.send_error(502)
        except (BrokenPipeError, ConnectionResetError):
            # The supervisor may already have stopped DSH at the case deadline.
            record['delivery_status'] = 'client_disconnected'

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
        record['budget_remaining_seconds'] = round(max(0, remaining), 3)
        if remaining <= 0:
            raise CaseDeadlineError('DSH 整例检视时限已到')
        with httpx.Client(timeout=remaining, follow_redirects=False, trust_env=False) as client:
            with client.stream('POST', self.server.endpoint + '/chat/completions', json=payload,
                               headers={'Authorization': 'Bearer ' + self.server.token}) as reply:
                record.update(status='receiving', http_status=reply.status_code, response_bytes=0,
                              headers_seconds=round(time.monotonic() - self.request_started, 3))
                save(self.server.evidence, self.server.records)
                body = _bounded_response(reply, self.server.deadline, record, self.request_started)
        observed = response_observations(body, reply.headers.get('Content-Type', 'application/json'),
                                         self.server.redact)
        record.update(status='responded', http_status=reply.status_code,
                      usage=observed.pop('usage'), wire_response=observed)
        self.send_response(reply.status_code)
        self.send_header('Content-Type', reply.headers.get('Content-Type', 'application/json'))
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        # Structured, redacted evidence is saved above; avoid raw HTTP stderr logs.
        pass


def _bounded_response(reply, deadline, record, started):
    parts, size = [], 0
    for chunk in reply.iter_bytes():
        size += len(chunk)
        record['response_bytes'] = size
        record.setdefault('first_body_seconds', round(time.monotonic() - started, 3))
        if size > 16 * 1024 * 1024:
            raise ResponseLimitError('模型响应超过 16MiB 大小上限')
        if time.monotonic() >= deadline:
            raise CaseDeadlineError('读取模型响应时 DSH 整例检视时限已到')
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
