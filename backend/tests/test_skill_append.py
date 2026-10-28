"""Append protocol faults, legacy reads, cold races and frozen identity preservation."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import unittest
from unittest.mock import patch
import zipfile

from local_cos import LocalCOS
import test_skill_assets as assets
from webui import skill_append as append
from webui.skill_storage import SkillAssetStorage


class AppendPublicationTests(unittest.TestCase):
    request = assets.SkillAssetApiTests.request
    upload = assets.SkillAssetApiTests.upload

    def setUp(self):
        assets.SkillAssetApiTests.setUp(self)
        self.remote = LocalCOS(Path(self.temp.name) / 'remote', ignore_conditions=True)
        self.enterContext(patch('webui.case_storage.CaseArchiveStorage._create_client', return_value=self.remote))
        self.enterContext(patch.dict(os.environ, {
            'BENCH_CASE_STORAGE_MODE': 'cos', 'BENCH_CASE_S3_ACCESS_KEY_ID': 'local',
            'BENCH_CASE_S3_SECRET_ACCESS_KEY': 'local-only'}))
        self.storage = SkillAssetStorage(self.temp.name)

    def fault(self, **settings):
        (self.remote.root / 'fault.json').write_text(json.dumps(settings))

    def snapshot(self):
        return {str(p.relative_to(self.remote.root)): p.read_bytes()
                for p in (self.remote.root / 'objects').rglob('*') if p.is_file()}

    def seed_local(self):
        with patch.dict(os.environ, {'BENCH_CASE_STORAGE_MODE': 'local'}):
            self.assertEqual(self.upload(assets.package())['status'], 'succeeded')
            record = self.request('/api/skills').json['versions'][0]
        folder = Path(self.temp.name) / 'skill-assets/versions' / record['id']
        return folder, json.loads((folder / 'manifest.json').read_text())

    def fresh_restore(self):
        shutil.rmtree(self.dashboard.data / 'skill-assets', ignore_errors=True)
        return self.storage.restore(self.dashboard)

    def seed_legacy(self, folder, record, *, committed=True):
        archive = (folder / 'package.zip').read_bytes()
        value = dict(format='skill-assets-v1', manifest=record,
                     archive=dict(sha256=record['archive_sha256'], size=len(archive)))
        raw = append.encoded(value)
        commit = dict(format='skill-commit-v1', id=record['id'], record_sha256=hashlib.sha256(raw).hexdigest())
        objects = [('archives/' + record['archive_sha256'] + '.zip', archive), ('record.json', raw)]
        if committed:
            objects.append(('commit.json', append.encoded(commit)))
        for suffix, data in objects:
            self.remote.put_object(Bucket=self.storage.bucket, Key=self.storage._key_for(record['id'], suffix),
                                   Body=data, Metadata={'sha256': hashlib.sha256(data).hexdigest()})

    def test_legacy_committed_versions_restore_without_any_write_or_probe(self):
        folder, record = self.seed_local()
        self.seed_legacy(folder, record)
        before = self.snapshot()
        writes = len(self.remote.put_keys)
        self.assertEqual(self.fresh_restore(), 1)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.remote.put_keys), writes)
        self.assertEqual(self.request('/api/skills').json['versions'][0]['archive_sha256'], record['archive_sha256'])

    def test_uncommitted_legacy_record_is_not_repaired_or_made_selectable(self):
        folder, record = self.seed_local()
        self.seed_legacy(folder, record, committed=False)
        before = self.snapshot()
        self.assertEqual(self.fresh_restore(), 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.request('/api/skills').json['versions'], [])

    def test_reimport_and_new_content_never_replace_prior_publication_keys(self):
        original = assets.package()
        self.assertEqual(self.upload(original)['status'], 'succeeded')
        first = self.snapshot()
        self.assertEqual(self.upload(original)['status'], 'succeeded')
        self.assertEqual(self.snapshot(), first)
        changed = assets.package([('SKILL.md', assets.MAIN), ('references/rules.txt', b'changed')])
        self.assertEqual(self.upload(changed)['status'], 'succeeded')
        second = self.snapshot()
        self.assertTrue(all(second[key] == value for key, value in first.items()))
        self.assertEqual(len(self.remote.put_keys), len(set(self.remote.put_keys)))

    def test_partial_transaction_is_ignored_and_retry_gets_fresh_object_keys(self):
        self.fault(put='/commit.json')
        self.assertEqual(self.upload(assets.package())['status'], 'failed')
        partial = self.snapshot()
        self.assertTrue(partial)
        self.fault()
        self.assertEqual(self.storage.restore(self.dashboard), 0)
        self.assertEqual(self.snapshot(), partial)
        self.assertEqual(self.upload(assets.package())['status'], 'succeeded')
        self.assertTrue(all(self.snapshot()[key] == value for key, value in partial.items()))
        self.assertEqual(len(self.remote.put_keys), len(set(self.remote.put_keys)))

    def test_lost_put_responses_are_confirmed_by_full_readback_without_replay(self):
        for suffix in ('archive.zip', 'record.json', 'commit.json'):
            with self.subTest(suffix=suffix):
                self.fault(put_response=suffix)
                body = assets.package([('SKILL.md', assets.MAIN), ('references/rules.txt', suffix.encode())])
                self.assertEqual(self.upload(body)['status'], 'succeeded')
        self.assertEqual(len(self.remote.put_keys), len(set(self.remote.put_keys)))

    def test_truncated_archive_and_failed_record_readback_do_not_publish(self):
        for settings in (dict(head_size='.zip'), dict(get='/record.json')):
            with self.subTest(settings=settings):
                self.fault(**settings)
                self.assertEqual(self.upload(assets.package())['status'], 'failed')
                self.assertFalse(any(key.endswith('/commit.json') for key in self.remote.put_keys))
                self.fault()
                shutil.rmtree(self.remote.root / 'objects', ignore_errors=True)
        self.assertEqual(self.request('/api/skills').json['versions'], [])

    def test_missing_commit_with_local_confirmation_blocks_restore(self):
        self.assertEqual(self.upload(assets.package())['status'], 'succeeded')
        next(self.remote.root.glob('objects/**/append-v2/**/commit.json')).unlink()
        with self.assertRaisesRegex(RuntimeError, '版本记录缺失'):
            self.storage.restore(self.dashboard)

    def test_forged_commit_digest_or_cross_transaction_reference_is_rejected(self):
        self.assertEqual(self.upload(assets.package())['status'], 'succeeded')
        publication = append.catalog(self.storage)[0]
        ref = publication['commit']
        raw = append.read_verified(self.storage, ref, collect=True)
        value = json.loads(raw)
        value['record']['key'] = '../outside/record.json'
        forged = append.encoded(value)
        key = ref['key'].replace(ref['sha256'], hashlib.sha256(forged).hexdigest())
        self.remote.put_object(Bucket=self.storage.bucket, Key=key, Body=forged,
                               Metadata={'sha256': hashlib.sha256(forged).hexdigest()})
        with self.assertRaisesRegex(RuntimeError, '追加对象引用无效'):
            self.fresh_restore()

    def test_promotion_publishes_all_preexisting_repacked_originals_in_one_commit(self):
        folder, record = self.seed_local()
        original = (folder / 'package.zip').read_bytes()
        repacked = assets.package(compression=zipfile.ZIP_DEFLATED)
        with patch.dict(os.environ, {'BENCH_CASE_STORAGE_MODE': 'local'}):
            self.assertEqual(self.upload(repacked)['status'], 'succeeded')
        self.fault(put='/record.json')
        self.assertEqual(self.upload(original)['status'], 'failed')
        self.assertFalse(any(key.endswith('/commit.json') for key in self.remote.put_keys))
        self.fault()
        self.assertEqual(self.upload(original)['status'], 'succeeded')
        self.assertEqual(self.fresh_restore(), 1)
        version = self.request('/api/skills').json['versions'][0]
        self.assertEqual(set(version['archives']), {record['archive_sha256'], hashlib.sha256(repacked).hexdigest()})

    def test_cold_concurrent_different_primary_archives_fail_closed_without_replacement(self):
        folder, record = self.seed_local()
        other = Path(self.temp.name) / 'second-version'
        other.mkdir()
        body = assets.package(compression=zipfile.ZIP_DEFLATED)
        (other / 'package.zip').write_bytes(body)
        second = dict(record, archive_sha256=hashlib.sha256(body).hexdigest())
        original = append._put_json
        committed = threading.Barrier(2)

        def synchronized(storage, root, kind, value):
            ref = original(storage, root, kind, value)
            if kind == 'commit':
                committed.wait(timeout=10)
            return ref

        def publish(index):
            try:
                source, manifest = (folder, record) if index == 0 else (other, second)
                self.storage.store_version(source, manifest)
                return 'unexpected success'
            except RuntimeError:
                return 'conflict'

        with patch('webui.skill_append._put_json', side_effect=synchronized):
            with ThreadPoolExecutor(max_workers=2) as executor:
                self.assertEqual(list(executor.map(publish, range(2))), ['conflict', 'conflict'])
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, '冲突的冻结记录'):
            self.fresh_restore()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.remote.put_keys), len(set(self.remote.put_keys)))

    def test_old_and_new_conflicting_identities_are_not_resolved_by_sort_order(self):
        folder, record = self.seed_local()
        self.seed_legacy(folder, record)
        body = assets.package(compression=zipfile.ZIP_DEFLATED)
        other = Path(self.temp.name) / 'other.zip'
        other.write_bytes(body)
        append.publish(self.storage, other, dict(record, archive_sha256=hashlib.sha256(body).hexdigest()), 'version')
        with self.assertRaisesRegex(RuntimeError, '冲突的冻结记录'):
            self.fresh_restore()

    def test_record_reference_limits_and_publication_cap_fail_closed(self):
        self.assertEqual(self.upload(assets.package())['status'], 'succeeded')
        with patch('webui.skill_append.MAX_PUBLICATIONS', 0):
            with self.assertRaisesRegex(RuntimeError, '恢复上限'):
                self.fresh_restore()
        for ref in (dict(key='x', sha256='x', size=1), dict(key='x', sha256='a' * 64, size=True)):
            with self.assertRaises(RuntimeError):
                append.validate_reference('root/', 'record', ref, 10)
