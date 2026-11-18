"""Verify the DSH relay bounds stream stalls and retries empty completions.

Runs the production ``gateway.Handler`` against a loopback mock upstream; no
DSH runtime, real model or COS is involved.  Scenarios:

  healthy    one complete SSE text turn          -> exactly one transmission
  slow       deltas every 0.5 s for 3 s          -> one transmission (no false stall)
  stall      200 headers then silence            -> bounded and retried
  keepalive  SSE comments only, never a data event -> bounded by progress, retried
  empty      completed turn with reasoning only  -> retried, next turn relayed

Only the new bounds are exercised: ``stall`` already worked before the change,
while ``keepalive`` and ``empty`` are the two shapes observed in
aacr-20261113064218441 that previously reached DSH's own 20-minute timeout or
its fail-closed compaction abort.

Usage:
    python tools/dsh_relay_probe.py [--read-timeout 1.5]
"""
import argparse
import json
import socket
import socketserver
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/src'))

if not hasattr(socketserver, 'ThreadingUnixStreamServer'):
    # Windows Python without AF_UNIX: the relay's socket server is swapped for
    # its TCP flavour; Handler.forward/_transmit/_bounded_response are unchanged.
    socketserver.ThreadingUnixStreamServer = type(
        'ThreadingUnixStreamServer', (socketserver.ThreadingMixIn, socketserver.TCPServer),
        dict(daemon_threads=True, allow_reuse_address=True))

from reviewers.dsh_skill import gateway  # noqa: E402

DONE = b'data: [DONE]\n\n'


def event(payload):
    return ('data: ' + json.dumps(payload) + '\n\n').encode()


def chunk(delta, finish=None):
    return event(dict(id='chatcmpl-probe', object='chat.completion.chunk', created=1,
                      model='bench-model',
                      choices=[dict(index=0, delta=delta, finish_reason=finish)]))


HEALTHY = chunk(dict(role='assistant', content='all good')) + chunk({}, 'stop') + DONE
EMPTY = chunk(dict(role='assistant', reasoning_content='thinking only')) + chunk({}, 'stop') + DONE


class MockHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', '0')))
        mode = self.server.mode
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.server.hits += 1
        if mode == 'stall':
            time.sleep(30)
        elif mode == 'keepalive':
            self._keepalive()
        elif mode == 'empty':
            self._write(HEALTHY if self.server.hits > 1 else EMPTY)
        elif mode == 'slow':
            self._slow()
        else:
            self._write(HEALTHY)

    def _write(self, data):
        try:
            self.wfile.write(data)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _keepalive(self):
        for _ in range(30):
            self._write(b': keepalive\n\n')
            time.sleep(0.4)

    def _slow(self):
        for _ in range(6):
            self._write(chunk(dict(content='ok ')))
            time.sleep(0.5)
        self._write(chunk({}, 'stop') + DONE)

    def log_message(self, *args):
        # The probe reports one result per scenario. Suppress the mock HTTP access log.
        pass


def _client_reply(server):
    body = json.dumps(dict(model='bench-model',
                           messages=[dict(role='user', content='hi')])).encode()
    client = socket.create_connection(('127.0.0.1', server.server_address[1]), timeout=12)
    client.sendall(b'POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\n'
                   b'Content-Type: application/json\r\nContent-Length: '
                   + str(len(body)).encode() + b'\r\n\r\n' + body)
    chunks = []
    try:
        while data := client.recv(1 << 16):
            chunks.append(data)
    except socket.timeout:
        return None
    return b''.join(chunks).decode('latin-1')


def run(scenario, read_timeout):
    """Run one scenario and return (passed, detail)."""
    upstream = ThreadingHTTPServer(('127.0.0.1', 0), MockHandler)
    upstream.mode, upstream.hits = scenario, 0
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    work = Path(tempfile.mkdtemp(prefix='relay-probe-'))
    evidence = work / 'requests.json'
    server = gateway.Relay(('127.0.0.1', 0), gateway.Handler)
    server.endpoint = f'http://127.0.0.1:{upstream.server_port}/v1'
    server.token, server.model = 'token', 'bench-model'
    server.deadline, server.evidence, server.records = time.monotonic() + 60, evidence, []
    server.retry = gateway.RetryPlan(max_attempts=2, base_delay_ms=100, max_delay_ms=100)
    server.read_timeout_s = read_timeout
    server.input_evidence, server.inputs = work / 'model-inputs.json', []
    server.redact = lambda text: text
    threading.Thread(target=server.serve_forever, daemon=True).start()
    started = time.monotonic()
    try:
        reply = _client_reply(server)
    finally:
        elapsed = time.monotonic() - started
        upstream.shutdown()
        server.shutdown()
    if reply is None:
        return False, f'no complete reply after {elapsed:.1f}s'
    records = json.loads(evidence.read_text())
    transmissions = len(records[0].get('transmissions', []))
    detail = (f'{elapsed:.1f}s, upstream hits={upstream.hits}, '
              f'transmissions={transmissions}, status={records[0].get("status")}')
    if scenario == 'healthy':
        return upstream.hits == 1 and 'all good' in reply, detail
    if scenario == 'slow':
        return (upstream.hits == 1 and elapsed >= 3.0
                and reply.count('"ok "') >= 5), detail
    if scenario == 'empty':
        return upstream.hits == 2 and 'all good' in reply, detail
    return elapsed < 8 and upstream.hits == 2, detail


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read-timeout', type=float, default=1.5)
    args = parser.parse_args()
    failed = []
    for scenario in ('healthy', 'slow', 'stall', 'keepalive', 'empty'):
        passed, detail = run(scenario, args.read_timeout)
        print(f'{"PASS" if passed else "FAIL"} {scenario}: {detail}')
        if not passed:
            failed.append(scenario)
    if failed:
        raise SystemExit('场景失败：' + '、'.join(failed))
    print('中继流预算与空内容重试场景全部通过（未调用真实模型/COS）。')


if __name__ == '__main__':
    main()
