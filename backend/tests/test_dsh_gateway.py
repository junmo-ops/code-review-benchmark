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


def stream(*events):
    return (''.join('data: ' + json.dumps(event) + '\r\n\r\n' for event in events)
            + 'data: [DONE]\r\n\r\n').encode()


class ResponseObservationTests(unittest.TestCase):
    def observe(self, body, content_type='text/event-stream', redact=lambda text: text):
        return gateway.response_observations(body, content_type, redact)

    def test_tool_fragments_do_not_become_tool_counts_or_visible_text(self):
        body = stream(
            dict(choices=[dict(delta=dict(reasoning_content='PRIVATE_REASONING'), finish_reason=None)]),
            dict(choices=[dict(delta=dict(tool_calls=[dict(index=0, id='same-call',
                 function=dict(name='read', arguments='PRIVATE_ARGUMENT'))]))]),
            dict(choices=[dict(delta=dict(tool_calls=[dict(index=0, function=dict(arguments='tail'))]),
                 finish_reason='tool_calls')]),
            dict(choices=[], usage=dict(prompt_tokens=24670, completion_tokens=737, total_tokens=25407)))
        result = self.observe(body)
        self.assertEqual(result['tool_fragments'], 2)
        self.assertEqual(result['reasoning_chars'], len('PRIVATE_REASONING'))
        self.assertFalse(result['delta_nonblank'])
        self.assertEqual(result['finish_reason'], 'tool_calls')
        self.assertEqual(result['event_count'], 4)
        self.assertTrue(result['done_marker'])
        self.assertEqual(result['usage']['completion_tokens'], 737)
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_message_text_and_nonstring_content_are_distinguished_from_delta_text(self):
        body = stream(dict(choices=[dict(delta=dict(content=[dict(text='PRIVATE_TEXT')]),
                                        message=dict(content='PRIVATE_FINAL'), finish_reason='stop')]))
        result = self.observe(body)
        self.assertEqual(result['delta_chars'], 0)
        self.assertTrue(result['message_nonblank'])
        self.assertEqual(result['message_chars'], len('PRIVATE_FINAL'))
        self.assertEqual(result['nonstring_content'], 1)
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_multiline_sse_uses_the_sdk_parser_and_retains_usage_only_events(self):
        body = (b': keepalive\r\n\r\ndata: {"choices":[{"delta":{"content":"ok"},\r\n'
                b'data: "finish_reason":"stop"}]}\r\n\r\n'
                b'data: {"usage":{"total_tokens":5}}\r\n\r\ndata: [DONE]\r\n\r\n')
        result = self.observe(body, 'text/event-stream; charset=utf-8')
        self.assertTrue(result['delta_nonblank'])
        self.assertEqual(result['finish_reason'], 'stop')
        self.assertEqual(result['usage'], dict(total_tokens=5))
        self.assertEqual(result['parse_status'], 'ok')

    def test_usage_and_finish_reason_never_copy_arbitrary_upstream_strings(self):
        body = json.dumps(dict(choices=[dict(message=dict(content=None), finish_reason='PRIVATE_FINISH')],
            usage=dict(prompt_tokens=12, completion_tokens=True, total_tokens=-1, secret='PRIVATE_USAGE',
                       prompt_tokens_details=dict(cached_tokens=4, created_cache_tokens=2, secret='PRIVATE'),
                       completion_tokens_details=dict(reasoning_tokens=6, secret='PRIVATE')))).encode()
        result = self.observe(body, 'application/json')
        self.assertEqual(result['usage'], dict(prompt_tokens=12,
            prompt_tokens_details=dict(cached_tokens=4, created_cache_tokens=2),
            completion_tokens_details=dict(reasoning_tokens=6)))
        self.assertEqual(result['finish_reason'], 'other')
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_response_id_is_redacted_before_truncation(self):
        token = 'PRIVATE_TOKEN_' * 30
        body = stream(dict(id='chatcmpl-' + token, choices=[]))
        result = self.observe(body, redact=lambda text: text.replace(token, '<REDACTED>'))
        self.assertEqual(result['response_id'], 'chatcmpl-<REDACTED>')

    def test_malformed_and_non_json_events_are_reported_without_raising_or_echoing_content(self):
        for body in (b'data: PRIVATE_REASONING\n\n', b'data: \xff\n\n',
                     b'data: []\n\n', b'data: {"choices":[1]}\n\n'):
            with self.subTest(body=body):
                result = self.observe(body)
                self.assertEqual(result['parse_status'], 'malformed')
                self.assertGreater(result['parse_errors'], 0)
                self.assertNotIn('PRIVATE', json.dumps(result))

    def test_absent_done_and_absent_events_are_not_invented(self):
        result = self.observe(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n')
        self.assertFalse(result['done_marker'])
        self.assertEqual(result['finish_reason'], 'stop')
        self.assertEqual(self.observe(b'')['parse_status'], 'no_events')


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
    def request(self, socket, timeout=5, stream=True):
        transport = httpx.HTTPTransport(uds=str(socket), retries=0)
        with httpx.Client(transport=transport, timeout=timeout) as client:
            return client.post('http://localhost/v1/chat/completions', json=dict(
                model='dsh-empty', messages=[dict(role='user', content='local fixture')], stream=stream))

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

    def test_actual_reasoning_only_stop_is_observed_without_replay_or_response_changes(self):
        message = dict(content='\n\n\n', reasoning_content='PRIVATE_REASONING')
        with tempfile.TemporaryDirectory() as name, serve(lambda body, index: (message, 'stop')) as model:
            root = Path(name)
            evidence = root / 'case.requests.json'
            with gateway.serve(root / 'model.sock', model.url, 'local-secret', 'dsh-empty',
                               time.monotonic() + 10, evidence) as records:
                reply = self.request(root / 'model.sock')
            wire = records[0]['wire_response']
            self.assertEqual(reply.status_code, 200)
            self.assertIn(b'PRIVATE_REASONING', reply.content)
            self.assertEqual(wire['delta_chars'], 3)
            self.assertFalse(wire['delta_nonblank'])
            self.assertEqual(wire['reasoning_chars'], len('PRIVATE_REASONING'))
            self.assertEqual(wire['finish_reason'], 'stop')
            self.assertTrue(wire['done_marker'])
            self.assertEqual(wire['parse_status'], 'ok')
            self.assertEqual(records[0]['usage'], model.usage)
            self.assertEqual(len(model.requests), 1)
            self.assertNotIn('PRIVATE_REASONING', evidence.read_text())

    def test_nonstream_reply_is_forwarded_unchanged_and_observed_without_extra_requests(self):
        with tempfile.TemporaryDirectory() as name, serve() as model:
            root = Path(name)
            with gateway.serve(root / 'model.sock', model.url, 'local-secret', 'dsh-empty',
                               time.monotonic() + 10, root / 'requests.json') as records:
                reply = self.request(root / 'model.sock', stream=False)
            self.assertEqual(reply.status_code, 200)
            self.assertEqual(json.loads(reply.json()['choices'][0]['message']['content']),
                             dict(status='complete', comments=[]))
            self.assertEqual(records[0]['wire_response']['wire_format'], 'json-or-other')
            self.assertTrue(records[0]['wire_response']['message_nonblank'])
            self.assertEqual(records[0]['usage'], model.usage)
            self.assertEqual(len(model.requests), 1)

    def test_upstream_error_response_is_forwarded_without_retry_or_error_body_in_evidence(self):
        error = dict(error=dict(message='PRIVATE_ERROR', code='PRIVATE_CODE'))
        with tempfile.TemporaryDirectory() as name, serve(lambda body, index: (dict(http_error=error), 'stop')) as model:
            root = Path(name)
            evidence = root / 'requests.json'
            with gateway.serve(root / 'model.sock', model.url, 'local-secret', 'dsh-empty',
                               time.monotonic() + 10, evidence) as records:
                reply = self.request(root / 'model.sock')
            self.assertEqual(reply.status_code, 503)
            self.assertEqual(reply.json(), error)
            self.assertTrue(records[0]['wire_response']['upstream_error'])
            self.assertNotIn('PRIVATE', evidence.read_text())
            self.assertEqual(len(model.requests), 1)

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
