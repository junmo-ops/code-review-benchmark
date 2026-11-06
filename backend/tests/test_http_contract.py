"""Real loopback transport and production Flask share a single API contract."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from application.files import save
from webui.service import Dashboard
from webui.server import handler_for
from webui.wsgi import create_app


class SharedHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        env = patch.dict(os.environ, BENCH_DATA_ROOT=str(root), BENCH_CONFIG_ROOT=str(root / 'config'),
                         BENCH_CASE_UPLOAD='0', BENCH_SERVER_MODE='', BENCH_AUTH_MODE='proxy',
                         BENCH_PUBLIC_URL='https://review.test', BENCH_PROXY_TOKEN='p' * 40,
                         BENCH_CSRF_TOKEN='c' * 40, BENCH_FRONTEND_DIST=str(root / 'frontend'))
        env.start()
        self.addCleanup(env.stop)
        self.app = Dashboard(root)
        self.addCleanup(self.app.shutdown)
        save(root / 'runs/ccrab/history/config.json', dict(instance_id='case', review_model='test'))
        save(root / 'config/model.local.json', dict(LCR_LLM_TOKEN='private-model-token'))
        self.client = create_app(self.app, start_background=False).test_client()
        self.headers = {'X-Bench-Proxy-Token': 'p' * 40, 'X-Bench-User': 'operator',
                        'X-Bench-Role': 'operator', 'X-Dashboard-Token': 'c' * 40,
                        'Origin': 'https://review.test'}
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.app, 'local-token'))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def local(self, path, method='GET', body=None, headers=None):
        request_headers = {'X-Dashboard-Token': 'local-token', **(headers or {})}
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_summary_list_and_detail_match_on_local_and_production_routes(self):
        for path in ['/api/runs?page_size=1', '/api/run?key=ccrab/history']:
            with self.subTest(path=path):
                code, value = self.local(path)
                remote = self.client.get(path, base_url='https://review.test', headers=self.headers)
                self.assertEqual(code, 200)
                self.assertEqual(remote.status_code, code)
                self.assertEqual(remote.json, value)
        code, value = self.local('/api/state?view=summary')
        self.assertEqual(code, 200)
        self.assertEqual(value['runs'], [])

    def test_local_auth_and_pagination_fail_closed(self):
        self.assertEqual(self.local('/api/runs', headers={'X-Dashboard-Token': ''})[0], 403)
        self.assertEqual(self.local('/api/runs', headers={'Host': 'evil.test'})[0], 403)
        self.assertEqual(self.local('/api/runs', headers={'Origin': 'https://evil.test'})[0], 403)
        self.assertEqual(self.local('/api/runs?page_size=100000')[0], 400)
        self.assertEqual(self.local('/api/run?key=../outside')[0], 400)

    def test_malformed_and_oversized_requests_return_json_on_both_transports(self):
        for body, expected in [('{', 400), (json.dumps({'x': 'a' * 20000}), 413)]:
            with self.subTest(expected=expected):
                status, value = self.local('/api/jobs', 'POST', body, {'Content-Type': 'application/json'})
                reply = self.client.post('/api/jobs', base_url='https://review.test', headers=self.headers,
                                         content_type='application/json', data=body)
                self.assertEqual(status, expected)
                self.assertEqual(reply.status_code, expected)
                self.assertIn('error', value)
                self.assertEqual(value, reply.json)

    def test_unexpected_errors_are_json_and_secrets_are_redacted_from_logs(self):
        with patch.object(self.app.queries, 'page', side_effect=RuntimeError('private-model-token')):
            with self.assertLogs('webui.wsgi', level='ERROR') as logs:
                status, value = self.local('/api/runs')
        self.assertEqual(status, 500)
        self.assertNotIn('private-model-token', json.dumps(value) + '\n'.join(logs.output))


if __name__ == '__main__':
    unittest.main()
