"""One frozen model endpoint; bounded transient retries with no redirects.

The upstream platform gateway (LAILGW) intermittently fails healthy cases with
rate limits (425/429/432), server errors (any 5xx, including the 585/504
observed on this deployment) and truncated SSE streams. DSH itself is frozen
with ``retryPolicy maxRetries=0`` (ADR 0001), so each such hiccup used to kill
the whole case. The relay now retries only failures that produced no usable
completion, inside the whole-case deadline, and records every attempt in the
request evidence for audit. Client errors (4xx), bounded-response violations
and deadline expiry are never retried. DSH-side request retries stay disabled;
known usage is counted from every transmission, including failed attempts.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
import json
import random
import socketserver
import threading
import time
from urllib.parse import urlsplit
import httpx

from runner.storage import save
from .wire import response_observations

RATE_LIMIT_STATUSES = frozenset({425, 429, 432})
TRANSIENT_STATUSES = frozenset({408, 500, 502, 503, 504, 520, 522, 524})

# A model response that produced no body byte for this long is a stuck
# upstream (headers arrived, stream never started), and one that pauses
# between chunks this long is dead mid-stream.  Observed healthy first bytes
# arrive within ~6 s and healthy streams keep emitting; the vllm case streamed
# 31K reasoning tokens for 9+ minutes continuously.  The bound is applied as
# httpx's per-read timeout so it also covers the first byte, and it stays far
# below the DSH-side stream allowance (streamIdleTimeoutMs / bridge undici
# bounds, 20 min) so the relay -- which can retry -- decides first instead of
# DSH's client giving up on a recoverable hiccup.
READ_TIMEOUT_S = 240.0


class RetryPlan:
    """Bounded relay retries for transient upstream failures.

    ``max_attempts`` total transmissions per relayed request; failed attempts
    wait exponentially with jitter (rate-limit statuses prefer ``Retry-After``
    or ``rate_limit_wait_ms``) but never past the case deadline.
    """

    def __init__(self, max_attempts=3, base_delay_ms=1000, max_delay_ms=16000,
                 rate_limit_wait_ms=30000, jitter_ratio=0.15):
        self.max_attempts = max(1, int(max_attempts))
        self.base_delay_ms = max(0, float(base_delay_ms))
        self.max_delay_ms = max(0, float(max_delay_ms))
        self.rate_limit_wait_ms = max(0, float(rate_limit_wait_ms))
        self.jitter_ratio = max(0.0, float(jitter_ratio))


def response_usage(body):
    return response_observations(body, 'text/event-stream')['usage']


def _transmissions(records):
    return [attempt for record in records
            for attempt in record.get('transmissions', [record])]


def summarize(records):
    records = _transmissions(records)
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


def _transient_observed(status, content_type, body):
    """Classify a completed upstream transmission.

    Returns ``(None, observations)`` when the response is usable as-is and
    ``('rate_limit'|'server'|'truncated_stream', observations)`` when it failed
    without producing a usable completion and deserves a bounded retry. The
    observations are parsed once and reused by the caller.
    """
    observed = response_observations(body, content_type)
    if status in RATE_LIMIT_STATUSES:
        return 'rate_limit', observed
    if status == 408 or status in TRANSIENT_STATUSES or status >= 500:
        return 'server', observed
    if (content_type.lower().startswith('text/event-stream') and observed['event_count'] > 0
            and not observed['done_marker'] and observed['finish_reason'] is None):
        # Mirrors DSH's 'Stream ended without finish_reason': the upstream
        # stopped mid-stream, so the response is not a usable completion.
        return 'truncated_stream', observed
    return None, observed


def _retry_after_seconds(value):
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


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
        plan = self.server.retry
        record['transmissions'] = []
        for number in range(1, plan.max_attempts + 1):
            remaining = self.server.deadline - time.monotonic()
            record['budget_remaining_seconds'] = round(max(0, remaining), 3)
            if remaining <= 0:
                raise CaseDeadlineError('DSH 整例检视时限已到')
            transmission = dict(attempt=number, status='started')
            record['transmissions'].append(transmission)
            record['retries'] = number - 1
            save(self.server.evidence, self.server.records)
            try:
                status, content_type, body, retry_after = self._transmit(payload, remaining, transmission)
            except (httpx.TransportError, OSError, ValueError) as error:
                transmission.update(status='failed', error_kind=_error_kind(error, self.server.deadline))
                save(self.server.evidence, self.server.records)
                if isinstance(error, (httpx.TransportError, OSError)) and number < plan.max_attempts:
                    self._pause(plan, number, 'transport', None, record)
                    continue
                raise
            reason, observed = _transient_observed(status, content_type, body)
            transmission.update(status='failed' if reason else 'responded', kind=reason,
                                http_status=status, usage=observed.pop('usage'), wire_response=observed)
            save(self.server.evidence, self.server.records)
            if reason is None or number == plan.max_attempts:
                record.update(status='responded', http_status=status,
                              usage=transmission['usage'], wire_response=observed)
                self._relay(status, content_type, body)
                return
            self._pause(plan, number, reason, retry_after, record)

    def _transmit(self, payload, remaining, record):
        # The read timeout bounds both the first body byte and any pause
        # between bytes (httpx applies it per read).  Never let a stuck stream
        # exceed the DSH client's own allowance: with bounded retries the
        # relay must decide and recover before the client gives up.
        read_timeout = min(remaining, getattr(self.server, 'read_timeout_s', READ_TIMEOUT_S))
        client = httpx.Client(timeout=read_timeout, follow_redirects=False, trust_env=False)
        with client:
            with client.stream('POST', self.server.endpoint + '/chat/completions', json=payload,
                               headers={'Authorization': 'Bearer ' + self.server.token}) as reply:
                record.update(status='receiving', http_status=reply.status_code, response_bytes=0,
                              headers_seconds=round(time.monotonic() - self.request_started, 3))
                save(self.server.evidence, self.server.records)
                body = _bounded_response(reply, self.server.deadline, record, self.request_started)
                return (reply.status_code, reply.headers.get('Content-Type', 'application/json'),
                        body, _retry_after_seconds(reply.headers.get('Retry-After')))

    def _pause(self, plan, number, kind, retry_after, record):
        if kind == 'rate_limit':
            delay_ms = plan.rate_limit_wait_ms if retry_after is None else retry_after * 1000
        else:
            delay_ms = min(plan.base_delay_ms * (2 ** (number - 1)), plan.max_delay_ms)
        if plan.jitter_ratio:
            delay_ms *= max(0.0, 1.0 + random.uniform(-plan.jitter_ratio, plan.jitter_ratio))
        remaining_ms = (self.server.deadline - time.monotonic()) * 1000
        delay_ms = min(delay_ms, max(0.0, remaining_ms - 250))
        if delay_ms <= 0:
            raise CaseDeadlineError('DSH 整例检视时限已到')
        time.sleep(delay_ms / 1000)

    def _relay(self, status, content_type, body):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        # Structured, redacted evidence is saved above; avoid raw HTTP stderr logs.
        pass


def _bounded_response(reply, deadline, record, started):
    parts, size = [], 0
    try:
        for chunk in reply.iter_bytes():
            size += len(chunk)
            record['response_bytes'] = size
            record.setdefault('first_body_seconds', round(time.monotonic() - started, 3))
            if size > 16 * 1024 * 1024:
                raise ResponseLimitError('模型响应超过 16MiB 大小上限')
            parts.append(chunk)
            if time.monotonic() >= deadline:
                raise CaseDeadlineError('读取模型响应时 DSH 整例检视时限已到')
    finally:
        observed = response_observations(b''.join(parts), reply.headers.get('Content-Type', 'application/json'))
        record.update(usage=observed.pop('usage'), wire_response=observed)
    return b''.join(parts)


@contextmanager
def serve(socket, endpoint, token, model, deadline, evidence, redact=lambda text: text, retry=None,
          read_timeout_s=READ_TIMEOUT_S):
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None or parsed.query or parsed.fragment:
        raise ValueError('DSH 模型接口地址无效')
    with Relay(str(socket), Handler) as server:
        server.endpoint, server.token, server.model = endpoint.rstrip('/'), token, model
        server.deadline, server.evidence, server.records = deadline, evidence, []
        server.retry = retry if retry is not None else RetryPlan()
        server.read_timeout_s = float(read_timeout_s)
        server.input_evidence = evidence.with_name(evidence.name.removesuffix('.requests.json') + '.model-inputs.json')
        server.inputs, server.redact = [], redact
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.records
        finally:
            server.shutdown()
            thread.join(timeout=2)
