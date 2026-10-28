"""Model relay limits and disconnected clients, without external model requests."""
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx

from reviewers.dsh_skill import gateway
from tools.dsh_local_model import serve


class GatewayFailureTests(unittest.TestCase):
    def test_deadline_error_after_client_exit_does_not_raise_secondary_broken_pipe(self):
        with tempfile.TemporaryDirectory() as name:
            handler = gateway.Handler.__new__(gateway.Handler)
            evidence = Path(name) / 'requests.json'
            handler.server = SimpleNamespace(records=[], evidence=evidence,
                                              deadline=time.monotonic() - 1)
            handler.forward = Mock(side_effect=ValueError('model response deadline'))
            handler.send_error = Mock(side_effect=BrokenPipeError('closed by supervisor'))
            handler.do_POST()
            records = json.loads(evidence.read_text())
            self.assertEqual(records[0]['status'], 'transport_or_contract_error')
            self.assertEqual(records[0]['error_kind'], 'case_deadline_exceeded')
            self.assertEqual(records[0]['delivery_status'], 'client_disconnected')
            self.assertGreaterEqual(records[0]['elapsed_seconds'], 0)
            self.assertEqual(handler.forward.call_count, 1)

    def test_deadline_and_size_limits_are_distinguishable(self):
        for content, deadline, error, kind in [
            (b'a', time.monotonic() - 1, gateway.CaseDeadlineError, 'case_deadline_exceeded'),
            (b'a' * (16 * 1024 * 1024 + 1), time.monotonic() + 60,
             gateway.ResponseLimitError, 'response_too_large'),
        ]:
            with self.subTest(kind=kind):
                record = {}
                reply = SimpleNamespace(iter_bytes=lambda: iter([content]))
                with self.assertRaises(error) as caught:
                    gateway._bounded_response(reply, deadline, record, time.monotonic())
                self.assertEqual(gateway._error_kind(caught.exception, deadline), kind)
                self.assertEqual(record['response_bytes'], len(content))

    def test_response_bytes_and_usage_are_preserved(self):
        content = b'data: {"usage":{"total_tokens":5}}\n\ndata: [DONE]\n\n'
        reply = SimpleNamespace(iter_bytes=lambda: iter([content[:7], content[7:]]))
        record = {}
        result = gateway._bounded_response(reply, time.monotonic() + 10, record, time.monotonic())
        self.assertEqual(result, content)
        self.assertEqual(gateway.response_usage(result), {'total_tokens': 5})
        self.assertEqual(record['response_bytes'], len(content))

    def test_error_diagnostics_do_not_store_raw_credentials_or_upstream_exception_text(self):
        with tempfile.TemporaryDirectory() as name:
            for error in [httpx.ReadTimeout('secret URL/token'), gateway.ResponseLimitError('secret'),
                          ValueError('secret request')]:
                handler = gateway.Handler.__new__(gateway.Handler)
                handler.server = SimpleNamespace(records=[], evidence=Path(name) / 'requests.json',
                                                  deadline=time.monotonic() + 60)
                handler.forward = Mock(side_effect=error)
                handler.send_error = Mock()
                handler.do_POST()
                self.assertNotIn('secret', handler.server.evidence.read_text())
                self.assertEqual(len(handler.server.records), 1)
                handler.forward.assert_called_once()
                handler.send_error.assert_called_once_with(502)


class GatewaySocketTests(unittest.TestCase):
    def request(self, socket, timeout=5):
        transport = httpx.HTTPTransport(uds=str(socket), retries=0)
        with httpx.Client(transport=transport, timeout=timeout) as client:
            return client.post('http://localhost/v1/chat/completions', json=dict(
                model='dsh-empty', messages=[dict(role='user', content='local fixture')], stream=True))

    def test_real_relay_preserves_response_and_records_timing_without_credentials(self):
        with tempfile.TemporaryDirectory() as name, serve() as model:
            root = Path(name)
            evidence = root / 'case.requests.json'
            with gateway.serve(root / 'model.sock', model.url, 'local-secret', 'dsh-empty',
                               time.monotonic() + 10, evidence) as records:
                reply = self.request(root / 'model.sock')
            self.assertEqual(reply.status_code, 200)
            self.assertEqual(len(model.requests), 1)
            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertEqual(record['status'], 'responded')
            self.assertEqual(record['response_bytes'], len(reply.content))
            self.assertEqual(record['usage'], model.usage)
            self.assertGreaterEqual(record['elapsed_seconds'], record['headers_seconds'])
            self.assertGreaterEqual(record['first_body_seconds'], record['headers_seconds'])
            self.assertNotIn('local-secret', evidence.read_text())

    def test_real_client_disconnect_does_not_trigger_thread_exception_or_replay(self):
        entered, release = threading.Event(), threading.Event()

        def blocked(body, index):
            entered.set()
            release.wait(5)
            return dict(content='{"status":"complete","comments":[]}'), 'stop'

        with tempfile.TemporaryDirectory() as name, serve(blocked) as model:
            root = Path(name)
            with patch.object(gateway.Relay, 'handle_error') as unhandled, gateway.serve(
                    root / 'model.sock', model.url, 'local-secret', 'dsh-empty',
                    time.monotonic() + 10, root / 'requests.json') as records:
                try:
                    with self.assertRaises(httpx.ReadTimeout):
                        self.request(root / 'model.sock', timeout=.2)
                    self.assertTrue(entered.wait(2))
                finally:
                    release.set()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and 'elapsed_seconds' not in records[0]:
                    time.sleep(.01)
            self.assertEqual(records[0]['delivery_status'], 'client_disconnected')
            self.assertEqual(records[0]['status'], 'responded')
            self.assertEqual(len(model.requests), 1)
            unhandled.assert_not_called()

    def test_real_upstream_deadline_and_closed_client_keep_original_failure_evidence(self):
        entered, release = threading.Event(), threading.Event()

        def blocked(body, index):
            entered.set()
            release.wait(5)
            return dict(content='{"status":"complete","comments":[]}'), 'stop'

        with tempfile.TemporaryDirectory() as name, serve(blocked) as model:
            root = Path(name)
            with patch.object(gateway.Relay, 'handle_error') as unhandled, gateway.serve(
                    root / 'model.sock', model.url, 'local-secret', 'dsh-empty',
                    time.monotonic() + .8, root / 'requests.json') as records:
                try:
                    with self.assertRaises(httpx.ReadTimeout):
                        self.request(root / 'model.sock', timeout=.2)
                    self.assertTrue(entered.wait(2))
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline and 'elapsed_seconds' not in records[0]:
                        time.sleep(.01)
                finally:
                    release.set()
            record = records[0]
            self.assertEqual(record['status'], 'transport_or_contract_error')
            self.assertEqual(record['error_kind'], 'case_deadline_exceeded')
            self.assertEqual(record['delivery_status'], 'client_disconnected')
            self.assertEqual(len(model.requests), 1)
            unhandled.assert_not_called()


if __name__ == '__main__':
    unittest.main()
