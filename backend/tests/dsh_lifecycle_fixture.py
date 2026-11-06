"""Authenticated upload/queue fixture; every batch uses the complete imported 50 rows."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from dsh_probe import ROOT
from scripts.e2e.fixtures import create
from webui.queue import DurableDashboard
from webui.service import save
from webui.wsgi import create_app


class LifecycleFixture:
    def __init__(self, test, model, controlled=True):
        self.test = test
        self.model = model
        self.root = Path(test.enterContext(tempfile.TemporaryDirectory(prefix='dsh-lifecycle-')))
        test.enterContext(patch.dict(os.environ, dict(BENCH_PROJECT_ROOT=str(ROOT),
            BENCH_DATA_ROOT=str(self.root), BENCH_CONFIG_ROOT=str(self.root), BENCH_CASE_UPLOAD='1',
            BENCH_CASE_STORAGE_MODE='local', BENCH_RUN_STORAGE_MODE='local', BENCH_MODEL_STORAGE_MODE='local',
            BENCH_PUBLIC_URL='https://review.test', BENCH_PROXY_TOKEN='p' * 40, BENCH_CSRF_TOKEN='c' * 40,
            PYTHONPATH=os.pathsep.join([str(ROOT / 'backend/src'), str(ROOT / 'backend/tests'), str(ROOT)]))))
        save(self.root / 'model.local.json', dict(LCR_LLM_MODEL='dsh-review', LCR_LLM_TOKEN='local-only',
            LCR_LLM_BASE_URL=model.url, JUDGE_MODEL='dsh-judge', JUDGE_API_KEY='judge-only', JUDGE_BASE_URL=model.url))
        create(self.root)
        self.app = DurableDashboard(ROOT)
        self.client = create_app(self.app).test_client()
        self.headers = {'X-Bench-Proxy-Token': 'p' * 40, 'X-Bench-User': 'alice',
                        'X-Bench-Role': 'operator', 'X-Dashboard-Token': 'c' * 40, 'Origin': 'https://review.test'}
        self.import_file('repository', 'repository.zip')
        self.import_file('dataset', 'dsh-cases-50.zip')
        self.dataset = next(d['id'] for s in self.get('/api/state')['catalog'] for d in s['datasets']
                            if d.get('name') == 'DSH 本地50例')
        self.worker = None
        self.controlled = controlled
        test.addCleanup(self.close)
        self.start_worker()

    def request(self, path, method='GET', **kwargs):
        return self.client.open(path, method=method, base_url='https://review.test', headers=self.headers, **kwargs)

    def get(self, path):
        response = self.request(path)
        self.test.assertEqual(response.status_code, 200, response.json)
        return response.json

    def post(self, path, data=None):
        response = self.request(path, 'POST', json=data)
        self.test.assertIn(response.status_code, (200, 201, 202), response.json)
        return response.json

    def import_file(self, kind, name):
        data = (self.root / 'fixtures' / name).read_bytes()
        upload = self.post('/api/uploads/start', dict(kind=kind, filename=name, size=len(data),
                           fingerprint=hashlib.sha256(data).hexdigest()))['upload']['id']
        response = self.request(f'/api/uploads/{upload}/chunks/0', 'POST', data=data,
                                content_type='application/octet-stream')
        self.test.assertEqual(response.status_code, 200, response.json)
        task = self.post(f'/api/uploads/{upload}/complete')['task']
        self.wait(lambda: self.get('/api/cases/imports?id=' + task['id'])['task']['status'] not in {'queued', 'running'})
        self.test.assertEqual(self.get('/api/cases/imports?id=' + task['id'])['task']['status'], 'succeeded')

    def start_worker(self):
        log = self.test.enterContext((self.root / 'worker.log').open('a'))
        command = ([sys.executable, str(Path(__file__).with_name('dsh_controlled.py')), '--worker']
                   if self.controlled else [sys.executable, '-m', 'webui.worker'])
        self.worker = subprocess.Popen(command, env=dict(os.environ), stdout=log, stderr=subprocess.STDOUT)
        self.wait(lambda: self.get('/api/state')['worker_ready'] or self.get('/api/state')['recovery_required'])

    def wait(self, predicate, seconds=30):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.1)
        self.test.fail('Lifecycle observation deadline exceeded: ' + (self.root / 'worker.log').read_text()[-8000:])

    def create(self, name, **changes):
        return self.post('/api/jobs', dict(action='create', suite='aacr', dataset=self.dataset,
            name=name, reviewer='dsh_skill', skill_mode='baseline', evaluator='original',
            start_after_create=True, **changes))

    def job(self, created):
        return next(j for j in self.get('/api/state')['jobs'] if j['id'] == created['job_id'])

    def close(self):
        if self.worker and self.worker.poll() is None:
            self.worker.terminate()
            self.worker.wait(timeout=20)
        save(self.root / 'boundary.json', dict(
            executor='controlled sandbox program, not native DSH' if self.controlled else 'native pinned DSH',
            dataset='synthetic imported 50 unique case identities, not the user official selection',
            model_and_judge='loopback deterministic HTTP substitutes; no real model or COS',
            requests=len(self.model.requests)))
        save(self.root / 'observed-model-requests.json', self.model.requests)
        destination = os.getenv('DSH_EVIDENCE_ROOT')
        if destination:
            shutil.copytree(self.root, Path(destination) / ('lifecycle-' + self.root.name),
                            ignore=shutil.ignore_patterns('repos', 'demo'))
