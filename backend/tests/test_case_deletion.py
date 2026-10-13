"""Dataset removal purges shared assets and keeps historical runs viewable."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from webui import case_assets
from webui.case_storage import CaseAssetStorage
from webui.service import Dashboard, save

ROOT = Path(__file__).resolve().parents[2]


class DatasetDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / 'data'
        self.env = patch.dict(os.environ, {'BENCH_DATA_ROOT': str(self.data),
                                          'BENCH_CONFIG_ROOT': str(self.root / 'models'),
                                          'BENCH_CASE_STORAGE_MODE': 'local'})
        self.env.start()
        self.app = Dashboard(self.root)
        self.payload = (ROOT / 'data/pilot1.jsonl').read_bytes()
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.slug = 'cline__cline'
        self.dataset = self.data / 'case-datasets/aacr' / self.digest / 'dataset.jsonl'
        self.dataset.parent.mkdir(parents=True)
        self.dataset.write_bytes(self.payload)
        (self.data / 'repos' / self.slug / '.git').mkdir(parents=True)
        self.refs = {self.slug: {'sha256': 'a' * 64, 'size': 1, 'source': 'repository-pool'}}
        self.row = {'id': f'uploads/aacr/{self.digest}.jsonl', 'suite': 'aacr', 'name': 'one',
                    'count': 1, 'dataset_sha256': self.digest,
                    'file': self.dataset.relative_to(self.data).as_posix(),
                    'repositories': [self.slug], 'repository_versions': self.refs}
        save(case_assets._registry_path(self.data), {'datasets': [self.row]})
        self.record = {'format': 'code-review-benchmark-case-assets-v2', 'suite': 'aacr',
                       'dataset_sha256': self.digest, 'name': 'one', 'count': 1,
                       'dataset_size': len(self.payload), 'repositories': self.refs}
        self.storage = CaseAssetStorage.__new__(CaseAssetStorage)
        self.storage.data = self.data
        self.storage.prefix = 'assets'
        self.storage.bucket = 'test-bucket'
        self.storage.client = Mock()
        self.storage._save_local_record(self.data, self.record)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def history(self, filename='run-config.json'):
        path = self.data / 'runs/history' / filename
        save(path, {'dataset_sha256': self.digest, 'suite': {'id': 'aacr'}})
        return path

    def remove(self):
        return case_assets.manage_case_dataset(self.app, {'action': 'delete', 'id': self.row['id']})

    def test_used_dataset_is_purged_but_frozen_history_stays_viewable(self):
        history = self.history()
        frozen = history.read_bytes()
        result = self.remove()
        self.assertIn('不能续跑检视', result['message'])
        self.assertEqual(case_assets.registered_datasets(self.data), [])
        self.assertTrue(case_assets._registry(self.data)['datasets'][0]['archived'])
        self.assertTrue(case_assets._registry(self.data)['datasets'][0]['purged'])
        self.assertEqual(history.read_bytes(), frozen)
        self.assertFalse(self.dataset.exists())
        self.assertFalse(case_assets.status(self.root, self.data)['ready'])
        self.assertEqual(self.app.catalog()[0]['datasets'], [])
        with self.assertRaises(ValueError):
            self.app.dataset_path(self.row['id'])
        self.assertFalse(case_assets._repository_is_referenced(self.data, self.slug))

    def test_backend_rejects_rerun_for_a_purged_batch_before_queueing(self):
        self.history()
        self.remove()
        config = {'dataset_sha256': self.digest, 'suite': {'id': 'aacr'}}
        warning = ('该批次依赖的案例集或仓库资源已被永久清理，不能续跑检视或重新评分；'
                   '历史日志、答卷和已生成结果仍可查看。重新导入对应资源后请新建批次。')
        with patch.object(self.app, 'config', return_value=config):
            self.assertEqual(self.app.compatibility('history'), warning)
        with patch.object(self.app, 'config', return_value=config), \
             patch.object(self.app, 'environment', return_value={}), \
             patch.object(self.app, 'compatibility', return_value=warning):
            with self.assertRaisesRegex(ValueError, '不能续跑检视或重新评分'):
                self.app.submit({'action': 'review', 'key': 'history'})

    def test_unused_dataset_is_physically_removed(self):
        self.remove()
        self.assertFalse(self.dataset.exists())
        self.assertEqual(case_assets._registry(self.data)['datasets'], [])
        self.assertFalse(self.storage._local_record_path(self.data, 'aacr', self.digest).exists())

    def test_comparison_history_also_marks_dataset_purged(self):
        self.history('experiment.json')
        self.remove()
        self.assertFalse(self.dataset.exists())
        self.assertTrue(case_assets._registry(self.data)['datasets'][0]['archived'])
        self.assertTrue(case_assets._registry(self.data)['datasets'][0]['purged'])

    def test_cos_tombstone_survives_registry_reconstruction(self):
        self.history()
        published = {}
        def publish(key, stream, *args):
            published[key] = json.loads(stream.read())
        self.storage._put_stream = publish
        with patch('webui.case_assets.mode_is_cos', return_value=True), \
             patch('webui.case_storage.CaseAssetStorage', return_value=self.storage):
            self.remove()
        record = published[self.storage._dataset_prefix('aacr', self.digest) + 'record.json']
        self.assertTrue(record['archived'])
        self.assertTrue(record['purged'])
        case_assets._registry_path(self.data).unlink()
        self.storage._download_object = Mock()
        self.storage._restore_record_files(self.app, record)
        self.assertEqual(case_assets.registered_datasets(self.data), [])
        restored = case_assets._registry(self.data)['datasets'][0]
        self.assertTrue(restored['archived'])
        self.assertTrue(restored['purged'])
        self.assertEqual(restored['repository_versions'], self.refs)
        self.storage._download_object.assert_not_called()
        self.assertFalse(self.dataset.exists())

    def test_cos_failure_leaves_dataset_visible_and_intact(self):
        self.history()
        with patch('webui.case_assets.mode_is_cos', return_value=True), \
             patch('webui.case_storage.CaseAssetStorage') as factory:
            factory.return_value.purge_dataset.side_effect = RuntimeError('unavailable')
            with self.assertRaises(RuntimeError):
                self.remove()
        self.assertEqual(len(case_assets.registered_datasets(self.data)), 1)
        self.assertTrue(self.dataset.exists())

    def test_reimport_makes_archived_dataset_selectable_again(self):
        self.history()
        self.remove()
        def extract(archive, folder):
            dataset = folder / 'dataset.jsonl'
            dataset.write_bytes(self.payload)
            from runner.cloud import checked_instances
            return ({'format': case_assets.CASE_CONFIG_FORMAT, 'name': 'one'}, 'aacr',
                    checked_instances(dataset, 'aacr'), dataset, [self.slug])
        with patch.object(case_assets, '_extract_custom', side_effect=extract), \
             patch.object(case_assets, '_case_only_repositories', return_value=self.refs):
            case_assets._install_custom(self.app, self.root / 'dummy.zip')
        self.assertEqual(len(case_assets.registered_datasets(self.data)), 1)
        self.assertEqual(self.app.dataset_path(self.row['id']), self.dataset)

    def test_deleting_repository_purges_its_archived_dataset_dependency(self):
        self.history()
        self.remove()
        version = self.refs[self.slug]
        save(case_assets.repository_registry_path(self.data), {'repositories': {
            self.slug: {'repo': 'cline/cline', 'current': version['sha256'],
                        'versions': [dict(version, file=f'case-repositories/objects/{version["sha256"]}.bundle')]}}})
        case_assets.manage_repository(self.app, {'action': 'delete', 'slug': self.slug})
        row = case_assets._registry(self.data)['datasets'][0]
        self.assertTrue(row['purged'])
        self.assertNotIn(self.slug, case_assets.repository_registry(self.data)['repositories'])

    def test_unreadable_history_rejects_deletion(self):
        history = self.history()
        history.write_text('{broken')
        with self.assertRaisesRegex(ValueError, '为保护历史数据'):
            self.remove()
        self.assertTrue(self.dataset.exists())
        self.assertEqual(len(case_assets.registered_datasets(self.data)), 1)

    def test_queued_run_still_blocks_resource_mutation(self):
        self.app.jobs = {'queued': {'status': 'queued'}}
        with self.assertRaises(ValueError):
            self.remove()
        self.assertTrue(self.dataset.exists())
        self.assertEqual(len(case_assets.registered_datasets(self.data)), 1)
