import base64
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from webui.queue import DurableDashboard
from webui.wsgi import create_app
from regression_fixtures import frontend_environment


class ContainerAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            **frontend_environment(),
            'BENCH_DATA_ROOT': str(self.root / 'data'),
            'BENCH_CONFIG_ROOT': str(self.root / 'models'),
            'BENCH_PUBLIC_URL': 'https://review.test',
            'BENCH_AUTH_MODE': 'basic', 'BENCH_BASIC_USER': 'trial',
            'BENCH_BASIC_PASSWORD': 'private-password',
            'BENCH_CSRF_TOKEN': 'c' * 40,
            'BENCH_ENABLED_SUITES': 'aacr', 'BENCH_STORAGE_MODE': 'ephemeral',
            'BENCH_CONTAINER_ROLE': 'all',
        })
        self.env.start()
        self.dashboard = DurableDashboard(self.root)
        self.client = create_app(self.dashboard).test_client()
        self.headers = {
            'Authorization': 'Basic ' + base64.b64encode(b'trial:private-password').decode(),
            'X-Dashboard-Token': 'c' * 40,
        }

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_requires_login_and_rejects_forged_proxy_headers(self):
        spoof = {'X-Bench-User': 'trial', 'X-Bench-Role': 'operator',
                 'X-Bench-Proxy-Token': 'p' * 40}
        response = self.client.get('/', base_url='https://review.test', headers=spoof)
        self.assertEqual(response.status_code, 401)
        self.assertIn('Basic', response.headers['WWW-Authenticate'])
        self.assertEqual(self.client.get('/', base_url='https://review.test',
                                         headers=self.headers).status_code, 200)

    def test_csrf_origin_and_domain_still_apply(self):
        headers = {'Authorization': self.headers['Authorization']}
        self.assertEqual(self.client.get('/api/state', base_url='https://review.test',
                                         headers=headers).status_code, 403)
        headers.update(self.headers, Origin='https://evil.test')
        self.assertEqual(self.client.post('/api/jobs', base_url='https://review.test',
                                          headers=headers, json={}).status_code, 403)
        self.assertEqual(self.client.get('/', base_url='https://evil.test',
                                         headers=self.headers).status_code, 403)

    def test_minimal_public_health_and_honest_storage_state(self):
        self.assertEqual(self.client.get('/healthz').json, {'status': 'alive'})
        response = self.client.get('/readyz')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json, {'status': 'worker_unavailable'})
        self.dashboard.queue.meta('heartbeat', time.time())
        self.assertEqual(self.client.get('/healthz').status_code, 200)
        self.dashboard.queue.meta('recovery_required', True)
        self.assertEqual(self.client.get('/healthz').status_code, 200)
        self.assertEqual(self.client.get('/readyz').status_code, 503)
        response = self.client.get('/api/state', base_url='https://review.test',
                                   headers=self.headers)
        self.assertEqual(response.json['storage_mode'], 'ephemeral')
        self.assertEqual([c['id'] for c in response.json['catalog']], ['aacr'])

    def test_missing_password_fails_closed(self):
        with patch.dict(os.environ, {'BENCH_BASIC_PASSWORD': ''}):
            with self.assertRaises(RuntimeError):
                create_app(self.dashboard)


@unittest.skipIf(os.name == 'nt', 'Container supervisor uses POSIX sessions')
class ContainerProcessTests(unittest.TestCase):
    def test_failed_service_stops_other_service_and_separate_session_child(self):
        from deployment import container
        original_popen = subprocess.Popen
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / 'child.pid'
            worker_script = (
                'import subprocess,sys,pathlib,time; '
                'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"],start_new_session=True); '
                f'pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); '
                'time.sleep(60)'
            )
            web_script = (
                'import pathlib,time,sys; '
                f'p=pathlib.Path({str(pid_file)!r}); '
                '\nfor _ in range(100):\n if p.exists():break\n time.sleep(.05)\n'
                'sys.exit(7)'
            )
            def launch(command, **kwargs):
                if command[0] == 'ps':
                    return original_popen(command, **kwargs)
                script = 'pass' if '--prepare' in command else (
                    worker_script if 'webui.worker' in command else web_script)
                return original_popen([sys.executable, '-c', script], **kwargs)
            with patch.dict(os.environ, {'BENCH_CONTAINER_ROLE': 'all'}), \
                    patch.object(sys, 'argv', ['container']), \
                    patch('webui.wsgi.create_app'), \
                    patch('deployment.container.subprocess.Popen', side_effect=launch):
                with self.assertRaises(SystemExit) as exited:
                    container.main()
                self.assertEqual(exited.exception.code, 7)
            child = int(pid_file.read_text())
            for _ in range(50):
                result = subprocess.run(['ps', '-p', str(child), '-o', 'stat='], capture_output=True, text=True)
                if not result.stdout.strip() or result.stdout.strip().startswith('Z'):
                    break
                time.sleep(.05)
            else:
                os.kill(child, 9)
                self.fail('escaped-session child remained alive after service failure')


if __name__ == '__main__':
    unittest.main()
