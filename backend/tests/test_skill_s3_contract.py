"""Real boto3 HTTP boundary: append publications never overwrite prior transactions."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import unittest
from unittest.mock import patch

import test_skill_assets as assets
from skill_s3_http import serve_s3
from webui.service import Dashboard, save
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

    def test_ignored_headers_do_not_prevent_verified_append_only_import(self):
        remote = self.enterContext(serve_s3('ignore'))
        self.connection(remote)
        task = self.upload(assets.package())
        self.assertEqual(task['status'], 'succeeded', task)
        self.assertEqual(len(self.request('/api/skills').json['versions']), 1)
        puts = [r for r in remote.requests if r['method'] == 'PUT']
        self.assertEqual(len(puts), 3)
        self.assertTrue(all('/skills/append-v2/' in r['path'] for r in puts))
        self.assertTrue(all('if-none-match' not in r['headers'] and
                            'x-cos-forbid-overwrite' not in r['headers'] for r in puts))
        self.assertFalse(any(r['method'] == 'DELETE' for r in remote.requests))

    def test_rejected_conditionals_need_no_workaround_and_async_put_is_not_published(self):
        remote = self.enterContext(serve_s3('reject'))
        self.connection(remote)
        for mode in ('reject', 'racy'):
            with self.subTest(mode=mode):
                remote.mode = mode
                task = self.upload(assets.package())
                self.assertEqual(task['status'], 'succeeded', task)
        remote.mode = 'accepted'
        remote.requests.clear()
        task = self.upload(assets.package([('SKILL.md', assets.MAIN),
                                          ('references/rules.txt', b'async')]))
        self.assertEqual(task['status'], 'failed', task)
        puts = [r for r in remote.requests if r['method'] == 'PUT']
        self.assertFalse(any(r['path'].endswith('/commit.json') for r in puts))

    def test_all_published_bytes_have_content_addressed_keys_and_signed_digest_metadata(self):
        remote = self.enterContext(serve_s3('ignore'))
        self.connection(remote)
        self.assertEqual(self.upload(assets.package())['status'], 'succeeded')
        puts = [r for r in remote.requests if r['method'] == 'PUT']
        for row in puts:
            raw, digest = remote.objects[row['path']]
            self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
            self.assertEqual(row['path'].split('/')[-2], digest)
            self.assertIn('x-amz-meta-sha256', row['signed_headers'])

    def test_concurrent_clients_preserve_both_publications_without_replacing_records(self):
        with patch.dict(os.environ, {'BENCH_CASE_STORAGE_MODE': 'local'}):
            self.assertEqual(self.upload(assets.package())['status'], 'succeeded')
            version = self.request('/api/skills').json['versions'][0]
        remote = self.enterContext(serve_s3('ignore'))
        self.connection(remote)
        folder = Path(self.temp.name) / 'skill-assets/versions' / version['id']
        record = json.loads((folder / 'manifest.json').read_text())
        remote.record_heads = threading.Barrier(2)

        def publish(index):
            SkillAssetStorage(self.temp.name).store_version(folder, dict(record, created_at='writer-' + str(index)))
            return index

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(list(executor.map(publish, range(2))), [0, 1])
        writes = [r for r in remote.requests if r['method'] == 'PUT']
        self.assertEqual(len(writes), 6)
        self.assertEqual(len({r['path'] for r in writes}), 6)
        records = [json.loads(value[0])['manifest']['created_at']
                   for key, value in remote.objects.items() if key.endswith('/record.json')]
        self.assertEqual(set(records), {'writer-0', 'writer-1'})
        replacement = Path(self.temp.name) / 'replacement'
        with patch.dict(os.environ, {'BENCH_DATA_ROOT': str(replacement)}):
            dashboard = Dashboard(replacement)
            self.assertEqual(SkillAssetStorage(replacement).restore(dashboard), 1)
        restored = json.loads((replacement / 'skill-assets/versions' / version['id'] / 'manifest.json').read_text())
        self.assertEqual(restored['archive_sha256'], record['archive_sha256'])

    def test_http_restore_is_read_only_even_when_service_ignores_all_preconditions(self):
        remote = self.enterContext(serve_s3())
        self.connection(remote)
        original = assets.package()
        self.assertEqual(self.upload(original)['status'], 'succeeded')
        before = self.request('/api/skills').json['versions']
        saved = dict(remote.objects)
        shutil.rmtree(self.dashboard.data / 'skill-assets')
        remote.mode = 'ignore'
        remote.requests.clear()
        restore_case_archives(Path(self.temp.name))
        self.assertEqual(self.request('/api/skills').json['versions'], before)
        self.assertEqual(remote.objects, saved)
        self.assertTrue(all(r['method'] in {'HEAD', 'GET'} for r in remote.requests))
        identifier = before[0]['id']
        self.assertEqual(self.request('/api/skills/' + identifier + '/archive').data, original)

    def test_rewriting_record_and_metadata_cannot_forge_the_digest_in_its_object_path(self):
        remote = self.enterContext(serve_s3('ignore'))
        self.connection(remote)
        self.assertEqual(self.upload(assets.package())['status'], 'succeeded')
        key = next(key for key in remote.objects if key.endswith('/record.json'))
        raw = remote.objects[key][0].replace(b'Inspect code changes', b'Forged content!!!!!')
        remote.objects[key] = (raw, hashlib.sha256(raw).hexdigest())
        shutil.rmtree(self.dashboard.data / 'skill-assets')
        with self.assertRaisesRegex(RuntimeError, 'Skill.*恢复失败'):
            restore_case_archives(Path(self.temp.name))
        self.assertEqual(self.request('/api/skills').json['versions'], [])

    def test_empty_legacy_cos_startup_does_not_write_probe_objects(self):
        remote = self.enterContext(serve_s3('ignore'))
        self.connection(remote)
        self.assertEqual(restore_case_archives(Path(self.temp.name)), 0)
        self.assertFalse(any(row['method'] == 'PUT' for row in remote.requests))
