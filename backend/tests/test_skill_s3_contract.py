"""Real boto3 HTTP boundary: ignored preconditions must never reach Skill assets."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import threading
import unittest
from unittest.mock import patch

import test_skill_assets as assets
from skill_s3_http import serve_s3
from webui.service import save
from webui.skill_storage import SkillAssetStorage
from webui.case_storage import restore_case_archives


class SkillS3ContractTests(unittest.TestCase):
    request = assets.SkillAssetApiTests.request
    upload = assets.SkillAssetApiTests.upload

    def setUp(self):
        assets.SkillAssetApiTests.setUp(self)

    def connection(self, remote):
        config = Path(self.temp.name) / 'case-storage.json'
        save(config, dict(mode='cos', s3=dict(endpoint=remote.url, region='local', bucket='local-bucket',
             prefix='resource-contract', run_prefix='run-contract', timeout_ms=1000)))
        self.enterContext(patch('webui.case_storage.CONFIG_PATH', config))
        self.enterContext(patch.dict(os.environ, {
            'BENCH_CASE_STORAGE_MODE': 'cos', 'BENCH_CASE_S3_ACCESS_KEY_ID': 'local-only',
            'BENCH_CASE_S3_SECRET_ACCESS_KEY': 'local-only'}))

    def test_ignored_header_is_detected_before_any_skill_asset_write(self):
        remote = self.enterContext(serve_s3('ignore'))
        self.connection(remote)
        task = self.upload(assets.package())
        self.assertEqual(task['status'], 'failed', task)
        self.assertEqual(self.request('/api/skills').json['versions'], [])
        puts = [r for r in remote.requests if r['method'] == 'PUT']
        self.assertTrue(puts)
        self.assertTrue(all('/.write-probes/' in r['path'] for r in puts), puts)
        self.assertTrue(all(r['headers'].get('if-none-match') == '*' for r in puts), puts)

    def test_rejected_or_non_atomic_conditionals_do_not_publish_skill_assets(self):
        for mode in ('reject', 'racy', 'accepted'):
            with self.subTest(mode=mode), serve_s3(mode) as remote:
                self.connection(remote)
                task = self.upload(assets.package())
                self.assertEqual(task['status'], 'failed', task)
                self.assertEqual(self.request('/api/skills').json['versions'], [])
                puts = [r for r in remote.requests if r['method'] == 'PUT']
                self.assertTrue(puts)
                self.assertTrue(all('/.write-probes/' in r['path'] for r in puts), puts)

    def test_atomic_service_receives_real_conditional_headers_for_successful_import(self):
        remote = self.enterContext(serve_s3())
        self.connection(remote)
        task = self.upload(assets.package())
        self.assertEqual(task['status'], 'succeeded', task)
        self.assertEqual(len(self.request('/api/skills').json['versions']), 1)
        puts = [r for r in remote.requests if r['method'] == 'PUT']
        self.assertTrue(all(r['headers'].get('if-none-match') == '*' for r in puts), puts)
        self.assertTrue(all('if-none-match' in r['signed_headers'] for r in puts), puts)
        self.assertTrue(any('/.write-probes/' in r['path'] and r['status'] == 412 for r in puts), puts)

    def test_concurrent_real_clients_cannot_replace_an_immutable_version_record(self):
        with patch.dict(os.environ, {'BENCH_CASE_STORAGE_MODE': 'local'}):
            self.assertEqual(self.upload(assets.package())['status'], 'succeeded')
            version = self.request('/api/skills').json['versions'][0]
        remote = self.enterContext(serve_s3())
        self.connection(remote)
        folder = Path(self.temp.name) / 'skill-assets/versions' / version['id']
        record = json.loads((folder / 'manifest.json').read_text())
        remote.record_heads = threading.Barrier(2)

        def publish(index):
            storage = SkillAssetStorage(self.temp.name)
            try:
                storage.store_version(folder, dict(record, created_at='writer-' + str(index)))
                return index
            except RuntimeError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(publish, range(2)))
        winners = [result for result in results if result is not None]
        self.assertEqual(len(winners), 1, results)
        raw = next(value[0] for key, value in remote.objects.items() if key.endswith('/record.json'))
        self.assertEqual(json.loads(raw)['manifest']['created_at'], 'writer-' + str(winners[0]))
        writes = [r for r in remote.requests if r['method'] == 'PUT' and r['path'].endswith('/record.json')]
        self.assertEqual(sorted(r['status'] for r in writes), [200, 412])
        self.assertTrue(all(r['headers'].get('if-none-match') == '*' for r in writes), writes)

    def test_http_restore_rechecks_capability_and_preserves_confirmed_assets(self):
        remote = self.enterContext(serve_s3())
        self.connection(remote)
        original = assets.package()
        self.assertEqual(self.upload(original)['status'], 'succeeded')
        before = self.request('/api/skills').json['versions']
        saved = {key: value for key, value in remote.objects.items() if '/.write-probes/' not in key}
        shutil.rmtree(self.dashboard.data / 'skill-assets')
        remote.mode = 'ignore'
        with self.assertRaisesRegex(RuntimeError, '条件写能力未确认'):
            restore_case_archives(Path(self.temp.name))
        self.assertEqual(self.request('/api/skills').json['versions'], [])
        self.assertEqual(remote.objects, saved)
        remote.mode = 'atomic'
        restore_case_archives(Path(self.temp.name))
        self.assertEqual(self.request('/api/skills').json['versions'], before)
        base = '/api/skills/' + before[0]['id']
        self.assertEqual(self.request(base + '/archive').data, original)

    def test_empty_legacy_cos_startup_does_not_require_skill_conditional_writes(self):
        remote = self.enterContext(serve_s3('ignore'))
        self.connection(remote)
        self.assertEqual(restore_case_archives(Path(self.temp.name)), 0)
        self.assertFalse(any(row['method'] == 'PUT' for row in remote.requests))
