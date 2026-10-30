"""Single replay diagnostics against loopback HTTP only, never a real model."""
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'tools/dsh_replay_once.py'
RUN = 'aacr-20261029012553703-0231d10a'
CASE = 'CherryHQ__cherry-studio@5644b00'


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        self.server.requests.append((self.path, json.loads(body), self.headers.get('Authorization')))
        self.server.entered.set()
        self.server.release.wait(5)
        try:
            self.send_response(self.server.status)
            self.send_header('Content-Type', self.server.content_type)
            self.send_header('Location', self.server.url + '/must-not-follow')
            self.send_header('Content-Length', str(len(self.server.body)))
            self.end_headers()
            self.wfile.write(self.server.body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The alarm test deliberately closes the client connection.

    def log_message(self, *args):
        # Fixture request data is asserted in memory, not written to stderr.
        pass


@contextmanager
def server(body, status=200, content_type='text/event-stream', blocked=False):
    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as model:
        model.url = f'http://127.0.0.1:{model.server_port}'
        model.body, model.status, model.content_type = body, status, content_type
        model.requests, model.entered, model.release = [], threading.Event(), threading.Event()
        if not blocked:
            model.release.set()
        thread = threading.Thread(target=model.serve_forever, daemon=True)
        thread.start()
        try:
            yield model
        finally:
            model.release.set()
            model.shutdown()
            thread.join(timeout=2)


def sse(content=None, reasoning='PRIVATE_REASONING', multiline=False):
    delta = dict(reasoning_content=reasoning)
    if content is not None:
        delta['content'] = content
    events = [dict(id='chatcmpl-local-probe', choices=[dict(delta=delta, finish_reason=None)]),
              dict(choices=[dict(delta={}, finish_reason='stop')], usage=dict(
                  prompt_tokens=10, completion_tokens=8, total_tokens=18, unsafe='PRIVATE_USAGE'))]
    data = '\r\n\r\n'.join('data: ' + json.dumps(event) for event in events) + '\r\n\r\ndata: [DONE]\r\n\r\n'
    if multiline:
        data = data.replace('"choices":', '\r\ndata: "choices":', 1)
    return data.encode()


def quoted_inline_source():
    lines = [line for line in SCRIPT.read_text().splitlines()[1:] if line.strip()]
    literals = [json.dumps('\n'.join(lines[i:i + 2]) + '\n') for i in range(0, len(lines), 2)]
    return 'exec(\n' + '\n'.join(literals) + '\n)\n'


class ReplayProbeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.data = self.root / 'runtime'
        self.run = self.data / 'runs' / RUN
        (self.run / 'raw').mkdir(parents=True)
        self.configs = self.root / 'models'
        self.configs.mkdir()
        state = self.data / 'ui-state'
        state.mkdir()
        with sqlite3.connect(state / 'queue.sqlite3') as db:
            db.execute('CREATE TABLE jobs(run_key TEXT, job TEXT)')
            db.execute('INSERT INTO jobs VALUES(?,?)', (RUN, json.dumps({'profile': 'model.local.json'})))
        self.payload = dict(model='local-probe', stream=True, max_completion_tokens=31248,
                            messages=[dict(role='user', content='PRIVATE_PROMPT')], custom_field='unchanged')
        self.inputs = self.run / 'raw' / (CASE + '.model-inputs.json')
        self.inputs.write_text(json.dumps([{'never': 'replay-the-first-request'}, self.payload]))
        self.marker = Path(tempfile.gettempdir()) / ('dsh-replay-' + hashlib.sha256(
            (str(self.run) + CASE).encode()).hexdigest()[:24] + '.once')
        self.addCleanup(self.marker.unlink, missing_ok=True)
        self.receipt = self.marker.with_suffix('.wire-v2.jsonl')
        self.addCleanup(self.receipt.unlink, missing_ok=True)

    def configure(self, endpoint):
        cfg = dict(review_model='local-probe', review_endpoint_sha256=hashlib.sha256(endpoint.encode()).hexdigest())
        (self.run / 'run-config.json').write_text(json.dumps(cfg))
        (self.configs / 'model.local.json').write_text(json.dumps(dict(
            LCR_LLM_MODEL='local-probe', LCR_LLM_BASE_URL=endpoint, LCR_LLM_TOKEN='PRIVATE_TOKEN')))

    def invoke(self, flag=True, inline=False):
        command = [sys.executable, '-' if inline else str(SCRIPT)]
        if flag:
            command.append('--execute-once')
        env = dict(os.environ, BENCH_PROJECT_ROOT=str(self.root), BENCH_DATA_ROOT=str(self.data),
                   BENCH_CONFIG_ROOT=str(self.configs), OPENAI_LOG='debug',
                   PYTHONPATH=os.pathsep.join([str(ROOT / 'backend/src'), str(ROOT)]))
        text = '\n'.join(line for line in SCRIPT.read_text().splitlines()[1:] if line.strip()) if inline else None
        if inline == 'quoted':
            text = quoted_inline_source()
            text = '\n'.join(line.lstrip() for line in text.splitlines())
        result = subprocess.run(command, input=text, env=env, capture_output=True, text=True, timeout=10)
        for private in ('PRIVATE_TOKEN', 'PRIVATE_PROMPT', 'PRIVATE_REASONING', 'PRIVATE_ANSWER', 'PRIVATE_USAGE'):
            self.assertNotIn(private, result.stdout + result.stderr)
            if self.receipt.exists():
                self.assertNotIn(private, self.receipt.read_text())
        summary = json.loads(result.stdout[result.stdout.index('{'):])
        return result, summary

    def test_quote_wrapped_command_survives_a_terminal_that_strips_indentation(self):
        with server(sse('PRIVATE_ANSWER')) as model:
            self.configure(model.url + '/v1')
            result, summary = self.invoke(inline='quoted')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(summary['delta_nonblank'])
            self.assertEqual(model.requests, [('/v1/chat/completions', self.payload, 'Bearer PRIVATE_TOKEN')])
            repeated, cached = self.invoke(inline='quoted')
            self.assertEqual(repeated.returncode, 0)
            self.assertEqual(cached, summary)
            self.assertEqual(len(model.requests), 1)

    def test_exact_copied_inline_command_sends_only_last_payload_and_never_rewrites_batch(self):
        with server(sse('PRIVATE_ANSWER', multiline=True)) as model:
            self.configure(model.url + '/v1')
            before = {p.relative_to(self.run): p.read_bytes() for p in self.run.rglob('*') if p.is_file()}
            result, summary = self.invoke(inline=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(model.requests, [('/v1/chat/completions', self.payload, 'Bearer PRIVATE_TOKEN')])
            self.assertEqual(summary['delta_chars'], len('PRIVATE_ANSWER'))
            self.assertTrue(summary['delta_nonblank'])
            self.assertEqual(summary['reasoning_chars'], len('PRIVATE_REASONING'))
            self.assertEqual(summary['finish_reason'], 'stop')
            self.assertEqual(summary['usage'], dict(prompt_tokens=10, completion_tokens=8, total_tokens=18))
            self.assertEqual(before, {p.relative_to(self.run): p.read_bytes() for p in self.run.rglob('*') if p.is_file()})
            self.assertEqual(json.loads(self.receipt.read_text().splitlines()[-1]), summary)
            self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)
            repeated, cached = self.invoke(inline=True)
            self.assertEqual(repeated.returncode, 0)
            self.assertEqual(cached, summary)
            self.assertEqual(len(model.requests), 1)

    def test_legacy_empty_marker_is_preserved_and_new_attempt_result_is_saved(self):
        self.marker.touch(mode=0o600)
        with server(sse()) as model:
            self.configure(model.url + '/v1')
            result, summary = self.invoke(inline=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(summary['state'], 'completed')
            self.assertEqual(self.marker.read_bytes(), b'')
            self.assertEqual(len(model.requests), 1)

    def test_partial_or_empty_receipt_never_starts_another_call(self):
        with server(sse()) as model:
            self.configure(model.url + '/v1')
            for data in ('', '{"state":"sending","pid":123}\n{"incomplete":'):
                with self.subTest(data=data):
                    self.receipt.write_text(data)
                    result, summary = self.invoke(inline=True)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(summary['state'], {'reserved_or_incomplete', 'sending'})
            self.assertEqual(model.requests, [])

    def test_reasoning_only_stop_is_distinguished_from_visible_text(self):
        with server(sse()) as model:
            self.configure(model.url + '/v1')
            result, summary = self.invoke()
            self.assertEqual(result.returncode, 0)
            self.assertEqual(summary['delta_chars'], 0)
            self.assertFalse(summary['delta_nonblank'])
            self.assertEqual(summary['reasoning_chars'], len('PRIVATE_REASONING'))
            self.assertEqual(summary['finish_reason'], 'stop')
            self.assertEqual(len(model.requests), 1)

    def test_message_content_on_a_stream_is_reported_separately(self):
        body = b'data: {"choices":[{"message":{"content":"PRIVATE_ANSWER"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        with server(body) as model:
            self.configure(model.url + '/v1')
            result, summary = self.invoke()
            self.assertEqual(result.returncode, 0)
            self.assertEqual(summary['delta_chars'], 0)
            self.assertEqual(summary['message_chars'], len('PRIVATE_ANSWER'))
            self.assertTrue(summary['message_nonblank'])

    def test_non_stream_json_response_is_also_inspected_without_printing_content(self):
        body = json.dumps(dict(choices=[dict(message=dict(content='PRIVATE_ANSWER'), finish_reason='stop')])).encode()
        with server(body, content_type='application/json') as model:
            self.configure(model.url + '/v1')
            result, summary = self.invoke()
            self.assertEqual(result.returncode, 0)
            self.assertEqual(summary['wire_format'], 'json-or-other')
            self.assertTrue(summary['message_nonblank'])

    def test_http_failure_and_redirect_are_neither_retried_nor_followed(self):
        for status in (500, 302):
            with self.subTest(status=status), server(b'PRIVATE_TOKEN PRIVATE_ANSWER', status=status) as model:
                self.configure(model.url + '/v1')
                result, summary = self.invoke()
                self.assertEqual(result.returncode, 1)
                self.assertEqual(summary['http_status'], status)
                self.assertEqual(len(model.requests), 1)
                repeated, cached = self.invoke()
                self.assertEqual(repeated.returncode, 1)
                self.assertEqual(cached, summary)
                self.assertEqual(len(model.requests), 1)
                self.receipt.unlink()

    def test_missing_execution_consent_or_changed_endpoint_sends_nothing(self):
        with server(sse()) as model:
            self.configure(model.url + '/v1')
            result, summary = self.invoke(flag=False)
            self.assertEqual(summary['error'], 'PermissionError')
            cfg = self.run / 'run-config.json'
            value = json.loads(cfg.read_text())
            value['review_endpoint_sha256'] = '0' * 64
            cfg.write_text(json.dumps(value))
            result, summary = self.invoke()
            self.assertEqual(result.returncode, 1)
            self.assertEqual(summary['error'], 'ValueError')
            self.assertEqual(model.requests, [])
            self.assertFalse(self.marker.exists())

    def test_alarm_aborts_an_inflight_request_without_replay(self):
        with server(sse(), blocked=True) as model:
            self.configure(model.url + '/v1')
            env = dict(os.environ, BENCH_PROJECT_ROOT=str(self.root), BENCH_DATA_ROOT=str(self.data),
                       BENCH_CONFIG_ROOT=str(self.configs),
                       PYTHONPATH=os.pathsep.join([str(ROOT / 'backend/src'), str(ROOT)]))
            process = subprocess.Popen([sys.executable, str(SCRIPT), '--execute-once'], env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertTrue(model.entered.wait(5))
                os.kill(process.pid, signal.SIGALRM)
                out, err = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 1, out + err)
                summary = json.loads(out[out.index('{'):])
                self.assertIn(summary['error'], {'TimeoutError', 'ReadTimeout'})
                self.assertNotIn('PRIVATE_TOKEN', out + err)
                self.assertEqual(len(model.requests), 1)
                self.assertEqual(json.loads(self.receipt.read_text().splitlines()[-1]), summary)
                repeated, cached = self.invoke()
                self.assertEqual(repeated.returncode, 1)
                self.assertEqual(cached, summary)
                self.assertEqual(len(model.requests), 1)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()

    def test_concurrent_invocation_and_killed_sender_reuse_saved_state_without_replay(self):
        with server(sse(), blocked=True) as model:
            self.configure(model.url + '/v1')
            env = dict(os.environ, BENCH_PROJECT_ROOT=str(self.root), BENCH_DATA_ROOT=str(self.data),
                       BENCH_CONFIG_ROOT=str(self.configs),
                       PYTHONPATH=os.pathsep.join([str(ROOT / 'backend/src'), str(ROOT)]))
            with subprocess.Popen([sys.executable, str(SCRIPT), '--execute-once'], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
                try:
                    self.assertTrue(model.entered.wait(5))
                    repeated, inflight = self.invoke(inline=True)
                    self.assertEqual(repeated.returncode, 1)
                    self.assertEqual(inflight['state'], 'sending')
                    process.kill()
                    process.communicate(timeout=5)
                    repeated, saved = self.invoke(inline=True)
                    self.assertEqual(repeated.returncode, 1)
                    self.assertEqual(saved, inflight)
                    self.assertEqual(len(model.requests), 1)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)

    def test_malformed_event_never_echoes_payload_or_retries(self):
        with server(b'data: PRIVATE_TOKEN PRIVATE_REASONING\n\n') as model:
            self.configure(model.url + '/v1')
            result, summary = self.invoke()
            self.assertEqual(result.returncode, 1)
            self.assertEqual(summary['error'], 'JSONDecodeError')
            self.assertEqual(len(model.requests), 1)


if __name__ == '__main__':
    unittest.main()
