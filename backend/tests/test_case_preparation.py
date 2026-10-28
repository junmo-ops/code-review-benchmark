"""Built-in AACR selections restore uploaded Git histories before runner checks."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from local_cos import LocalCOS
from runner.cloud import checked_instances
from webui import case_assets
from webui.case_storage import CaseAssetStorage
from webui.service import Dashboard, save


class BuiltinRepositoryPreparationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.data = self.root / 'runtime'
        self.enterContext(patch.dict(os.environ, {
            'BENCH_DATA_ROOT': str(self.data), 'BENCH_CONFIG_ROOT': str(self.root / 'models'),
            'BENCH_CASE_STORAGE_MODE': 'cos', 'BENCH_CASE_S3_ACCESS_KEY_ID': 'local',
            'BENCH_CASE_S3_SECRET_ACCESS_KEY': 'local-only'}))
        self.remote = LocalCOS(self.root / 'remote')
        self.enterContext(patch('webui.case_storage.CaseArchiveStorage._create_client',
                                return_value=self.remote))
        self.app = Dashboard(self.root)
        self.repo = self.root / 'source'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Local preparation test')
        (self.repo / 'sample.py').write_text('value = 1\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'base')
        base = self.git('rev-parse', 'HEAD')
        (self.repo / 'sample.py').write_text('value = 2\n')
        self.git('commit', '-qam', 'head')
        self.head = self.git('rev-parse', 'HEAD')
        row = dict(instance_id='local.case', repo='cline/cline', base_commit=base,
                   head_commit=self.head, reference_comments=[])
        self.dataset = self.root / 'data/pilot1.jsonl'
        self.dataset.parent.mkdir()
        self.dataset.write_text(json.dumps(row) + '\n')
        bundle = self.root / 'source.bundle'
        self.git('bundle', 'create', str(bundle), '--all')
        payload = bundle.read_bytes()
        self.digest = hashlib.sha256(payload).hexdigest()
        self.version = dict(sha256=self.digest, size=len(payload),
                            file=f'case-repositories/objects/{self.digest}.bundle')
        save(case_assets.repository_registry_path(self.data), {'repositories': {
            'cline__cline': dict(repo='cline/cline', current=self.digest, versions=[self.version])}})
        storage = CaseAssetStorage(self.data)
        self.remote.put_object(Bucket=storage.bucket,
            Key=storage._repository_pool_key('cline__cline', self.digest),
            Body=payload, Metadata={'sha256': self.digest})
        self.spec = dict(suite='aacr', dataset='pilot1.jsonl', reviewer='dsh_skill')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args],
                                       stderr=subprocess.PIPE, text=True).strip()

    def prepare(self, spec=True, action='create'):
        self.app.prepare_case_repositories(self.spec if spec else None, 'batch', action)

    def assert_ready(self):
        destination = self.data / 'repos/cline__cline'
        self.assertTrue((destination / '.git').is_dir(), 'Built-in selection skipped Git restore')
        case_assets._validate_repository_cases(checked_instances(self.dataset, 'aacr'), destination)

    def test_builtin_selection_restores_uploaded_history_without_a_dataset_record(self):
        original = self.dataset.read_bytes()
        self.prepare()
        self.assert_ready()
        self.assertEqual(self.dataset.read_bytes(), original)

    def test_existing_batch_restores_from_its_frozen_case_list(self):
        save(self.app.run_dir('batch') / 'run-config.json', dict(
            suite={'id': 'aacr'}, reviewer='dsh_skill',
            dataset_sha256=hashlib.sha256(self.dataset.read_bytes()).hexdigest()))
        shutil.copyfile(self.dataset, self.app.run_dir('batch') / 'dataset.jsonl')
        self.dataset.write_text('not the frozen dataset')
        self.prepare(spec=False, action='check')
        destination = self.data / 'repos/cline__cline'
        self.assertTrue((destination / '.git').is_dir())
        result = subprocess.run(['git', '-C', str(destination), 'cat-file', '-e',
                                 self.head + '^{commit}'], capture_output=True, check=False)
        self.assertEqual(result.returncode, 0)

    def test_ready_repository_needs_no_remote_download_or_registry(self):
        self.prepare()
        self.assert_ready()
        case_assets.repository_registry_path(self.data).unlink()
        with patch.object(CaseAssetStorage, '_download_object', side_effect=AssertionError('download')):
            self.prepare()

    def test_missing_uploaded_repository_fails_before_init_with_actionable_message(self):
        case_assets.repository_registry_path(self.data).unlink()
        with self.assertRaisesRegex(ValueError, 'cline__cline'):
            self.prepare()
        self.assertFalse(self.app.run_dir('batch').exists())

    def test_missing_commit_is_not_replaced_by_another_case(self):
        row = json.loads(self.dataset.read_text())
        row['head_commit'] = 'f' * 40
        self.dataset.write_text(json.dumps(row) + '\n')
        with self.assertRaisesRegex(ValueError, '提交'):
            self.prepare()

    def test_damaged_frozen_dataset_stops_before_downloading(self):
        save(self.app.run_dir('batch') / 'run-config.json', dict(
            suite={'id': 'aacr'}, reviewer='dsh_skill', dataset_sha256='0' * 64))
        shutil.copyfile(self.dataset, self.app.run_dir('batch') / 'dataset.jsonl')
        with patch.object(CaseAssetStorage, '_download_object', side_effect=AssertionError('download')):
            with self.assertRaisesRegex(ValueError, '冻结案例清单'):
                self.prepare(spec=False, action='check')

    def test_remote_mapping_does_not_restore_local_repositories(self):
        self.spec['reviewer'] = 'server_agent'
        with patch.object(CaseAssetStorage, '_download_object', side_effect=AssertionError('download')):
            self.prepare()
        self.assertFalse((self.data / 'repos/cline__cline').exists())

    def test_published_dataset_uses_its_bound_version_not_repository_pool_current(self):
        digest = hashlib.sha256(self.dataset.read_bytes()).hexdigest()
        row = dict(id='uploads/aacr/' + digest + '.jsonl', suite='aacr', dataset_sha256=digest)
        save(case_assets._registry_path(self.data), {'datasets': [row]})
        self.spec['dataset'] = row['id']
        with patch.object(CaseAssetStorage, 'prepare_dataset_repositories') as prepare, \
             patch.object(CaseAssetStorage, 'prepare_builtin_repositories', side_effect=AssertionError('pool')):
            self.prepare()
        prepare.assert_called_once_with(self.data, 'aacr', digest, progress=None)


if __name__ == '__main__':
    unittest.main()
