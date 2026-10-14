"""Bounded lifecycle diagnostics. Never serialize requests, credentials or commands."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

_trace = ContextVar('benchmark_diagnostic_trace', default='')
_guard = threading.RLock()
_process = uuid.uuid4().hex
_FIELDS = {'job_id', 'key', 'stage', 'status', 'status_code', 'request_id', 'route',
           'elapsed_ms', 'error_type', 'signal', 'child_pid', 'exit_code', 'queued',
           'running', 'groups', 'updated_groups', 'processes', 'cgroup', 'interval_seconds',
           'process_count', 'rss_total_bytes', 'cpu_ticks', 'sample_seconds', 'action', 'group', 'bytes'}


def enabled():
    value = os.environ.get('BENCH_DIAGNOSTICS', os.environ.get('BENCH_SERVER_MODE', '0'))
    return value.strip().lower() in {'1', 'true', 'yes'}


def _memory():
    try:
        import resource
    except ImportError:
        return {}
    maximum = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'rss_peak_bytes': maximum if sys.platform == 'darwin' else maximum * 1024}


def _local_write(payload):
    from runner.runtime import data_root
    from runner.paths import PROJECT_ROOT
    directory = data_root(PROJECT_ROOT) / 'ui-state/diagnostics'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'events-{os.getpid()}.jsonl'
    with _guard:
        if path.exists() and path.stat().st_size + len(payload) > 4 * 1024 * 1024:
            path.replace(path.with_suffix('.previous.jsonl'))
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)


def event(name, **fields):
    if not enabled():
        return
    # Only call-site selected fields; never accept config, request bodies or exception text.
    record = dict(timestamp=datetime.now(timezone.utc).isoformat(), event=name,
                  pid=os.getpid(), process_id=_process,
                  boot_id=os.environ.get('BENCH_DIAGNOSTIC_BOOT_ID', ''), trace_id=_trace.get())
    record.update({key: value for key, value in fields.items() if key in _FIELDS})
    try:
        record.update(_memory())
        payload = (json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    except Exception:
        return  # Diagnostics must not change task execution on unsupported platforms.
    try:
        sys.stderr.write('[bench-diag] ' + payload.decode('utf-8'))
        sys.stderr.flush()
    except Exception:
        pass  # A closed platform log stream must not stop a benchmark.
    try:
        _local_write(payload)
    except Exception:
        pass  # Read-only/full disks must not turn telemetry into an application failure.


@contextmanager
def phase(name, **fields):
    started = time.monotonic()
    event(name + '.begin', **fields)
    try:
        yield
    except BaseException as error:
        event(name + '.failed', error_type=type(error).__name__,
              elapsed_ms=round((time.monotonic() - started) * 1000), **fields)
        raise
    else:
        event(name + '.end', elapsed_ms=round((time.monotonic() - started) * 1000), **fields)


def install_request_diagnostics(app):
    from flask import g, request

    @app.before_request
    def begin():
        if not enabled() or request.path not in {'/api/jobs', '/api/state', '/api/comparisons'}:
            return None
        identifier = uuid.uuid4().hex
        g.bench_diag = (_trace.set(identifier), time.monotonic(), identifier)
        event('http.begin', route=request.path, request_id=identifier)
        return None

    @app.after_request
    def finish(response):
        value = getattr(g, 'bench_diag', None)
        if value:
            _, started, identifier = value
            event('http.end', route=request.path, request_id=identifier,
                  status_code=response.status_code, elapsed_ms=round((time.monotonic() - started) * 1000))
            response.headers['X-Benchmark-Request-Id'] = identifier
        return response

    @app.teardown_request
    def cleanup(error):
        value = g.pop('bench_diag', None)
        if value:
            if error:
                event('http.failed', route=request.path, error_type=type(error).__name__)
            _trace.reset(value[0])
