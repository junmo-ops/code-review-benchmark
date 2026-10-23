"""Public imports and service-start recovery using only a local S3 substitute."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import unittest
import zipfile
from unittest.mock import patch

from local_cos import LocalCOS
import test_skill_assets as assets
from webui.case_storage import restore_case_archives
from webui.service import Dashboard
from webui.service import save
from webui.queue import DurableDashboard
from webui.wsgi import create_app
from deployment.run_storage import RunDataStorage, RunDataSynchronizer, restore_run_data
from dsh_probe import case
from tools.dsh_local_model import serve


class SkillPersistenceTests(unittest.TestCase):
    request = assets.SkillAssetApiTests.request
    upload = assets.SkillAssetApiTests.upload

    def setUp(self):
        assets.SkillAssetApiTests.setUp(self)
        self.remote = LocalCOS(Path(self.temp.name) / 'remote')
        self.enterContext(patch('webui.case_storage.CaseArchiveStorage._create_client',
                                return_value=self.remote))
        self.enterContext(patch.dict(os.environ, {
            'BENCH_CASE_STORAGE_MODE': 'cos', 'BENCH_CASE_S3_ACCESS_KEY_ID': 'local',
            'BENCH_CASE_S3_SECRET_ACCESS_KEY': 'local-only'}))

    def restart(self):
        restore_case_archives(Path(self.temp.name))
        self.dashboard = Dashboard(Path(self.temp.name))
        self.client = create_app(self.dashboard).test_client()

    def fault(self, **settings):
        (self.remote.root / 'fault.json').write_text(json.dumps(settings))

    def test_confirmed_import_restores_original_identity_and_resources_before_service(self):
        original = assets.package()
        self.assertEqual(self.upload(original)['status'], 'succeeded')
        version = self.request('/api/skills').json['versions'][0]
        shutil.rmtree(self.dashboard.data / 'skill-assets')
        self.restart()
        self.assertEqual(self.request('/api/skills').json['versions'], [version])
        base = '/api/skills/' + version['id']
        self.assertEqual(self.request(base + '/archive').data, original)
        self.assertEqual(self.request(base + '/file?path=references/rules.txt').data, b'original\r\n')

    def test_object_record_and_confirmation_failure_do_not_publish_then_retry_succeeds(self):
        for settings in [dict(put='.zip'), dict(put='/record.json'), dict(head_size='.zip')]:
            with self.subTest(settings=settings):
                self.fault(**settings)
                task = self.upload(assets.package())
                self.assertEqual(task['status'], 'failed', task)
                self.assertEqual(self.request('/api/skills').json['versions'], [])
                self.fault()
                shutil.rmtree(self.remote.root / 'objects', ignore_errors=True)
        self.assertEqual(self.upload(assets.package())['status'], 'succeeded')

    def test_failed_repacked_import_preserves_existing_version_and_both_originals_restore(self):
        original = assets.package()
        self.assertEqual(self.upload(original)['status'], 'succeeded')
        first = self.request('/api/skills').json['versions'][0]
        repacked = assets.package(compression=zipfile.ZIP_DEFLATED)
        self.fault(put='.zip')
        self.assertEqual(self.upload(repacked)['status'], 'failed')
        self.assertEqual(self.request('/api/skills').json['versions'], [first])
        self.fault()
        self.assertEqual(self.upload(repacked)['status'], 'succeeded')
        before = self.request('/api/skills').json['versions']
        shutil.rmtree(self.dashboard.data / 'skill-assets')
        self.restart()
        self.assertEqual(self.request('/api/skills').json['versions'], before)
        digest = hashlib.sha256(repacked).hexdigest()
        self.assertEqual(self.request('/api/skills/' + first['id'] + '/archive?sha256=' + digest).data, repacked)

    def test_restore_rejects_actual_corruption_missing_objects_and_local_conflicts(self):
        self.assertEqual(self.upload(assets.package())['status'], 'succeeded')
        version = self.request('/api/skills').json['versions'][0]
        root = self.dashboard.data / 'skill-assets'
        content = root / 'versions' / version['id'] / 'content/references/rules.txt'
        content.write_bytes(b'local immutable conflict')
        with self.assertRaisesRegex(RuntimeError, 'Skill.*恢复失败'):
            self.restart()
        self.assertEqual(content.read_bytes(), b'local immutable conflict')
        shutil.rmtree(root)
        self.fault(corrupt='.zip')
        with self.assertRaisesRegex(RuntimeError, 'Skill.*恢复失败'):
            self.restart()
        self.assertEqual(self.request('/api/skills').json['versions'], [])
        self.fault(get='.zip')
        with self.assertRaisesRegex(RuntimeError, 'Skill.*恢复失败'):
            self.restart()
        self.fault()
        self.restart()
        next(self.remote.root.glob('objects/**/record.json')).unlink()
        with self.assertRaisesRegex(RuntimeError, '版本记录缺失'):
            self.restart()

    def test_cos_mode_never_selects_an_unconfirmed_local_version(self):
        with patch.dict(os.environ, {'BENCH_CASE_STORAGE_MODE': 'local'}):
            self.assertEqual(self.upload(assets.package())['status'], 'succeeded')
        listing = self.request('/api/skills').json
        self.assertEqual(listing['versions'], [])
        self.assertEqual(len(listing['unavailable']), 1)
        self.assertEqual(self.upload(assets.package())['status'], 'succeeded')
        self.assertEqual(len(self.request('/api/skills').json['versions']), 1)

    def test_promoting_local_version_to_cos_retains_every_original_archive(self):
        original = assets.package()
        repacked = assets.package(compression=zipfile.ZIP_DEFLATED)
        with patch.dict(os.environ, {'BENCH_CASE_STORAGE_MODE': 'local'}):
            self.assertEqual(self.upload(original)['status'], 'succeeded')
            self.assertEqual(self.upload(repacked)['status'], 'succeeded')
            before = self.request('/api/skills').json['versions']
        self.assertEqual(self.upload(original)['status'], 'succeeded')
        shutil.rmtree(self.dashboard.data / 'skill-assets')
        self.restart()
        self.assertEqual(self.request('/api/skills').json['versions'], before)

    def test_oversized_version_record_is_rejected_before_cos_publication(self):
        prefix = ('a' * 200 + '/') * 11 + 'b' * 20 + '/'
        main = b'---\nname: bounded-record\ndescription: Review static changes\n---\nReview.\n'
        entries = [('SKILL.md', main)] + [(prefix + f'{index:04d}.txt', b'x') for index in range(1799)]
        task = self.upload(assets.package(entries))
        self.assertEqual(task['status'], 'failed', task)
        self.assertIn('4 MiB', task['message'])
        self.assertEqual(self.request('/api/skills').json['versions'], [])
        self.assertEqual(list(self.remote.root.glob('objects/**/skills/*/record.json')), [])
        self.assertEqual(self.upload(assets.package())['status'], 'succeeded')

    def test_concurrent_immutable_object_cannot_be_overwritten_during_import(self):
        self.fault(conflict='.zip')
        self.assertEqual(self.upload(assets.package())['status'], 'failed')
        self.assertEqual(self.request('/api/skills').json['versions'], [])
        self.assertEqual(next(self.remote.root.glob('objects/**/*.zip')).read_bytes(),
                         b'concurrent immutable object')

    def test_restore_rejects_repacked_archive_bound_to_different_content(self):
        self.assertEqual(self.upload(assets.package())['status'], 'succeeded')
        root = next(self.remote.root.glob('objects/**/skills/*/record.json')).parent
        wrong = assets.package([('SKILL.md', assets.MAIN), ('references/rules.txt', b'wrong identity')])
        digest = hashlib.sha256(wrong).hexdigest()
        bucket = self.remote.root / 'objects'
        relative = root.relative_to(next(bucket.iterdir())).as_posix()
        bucket_name = next(bucket.iterdir()).name
        self.remote.put_object(Bucket=bucket_name, Key=relative + '/archives/' + digest + '.zip',
                               Body=wrong, Metadata={'sha256': digest})
        raw = json.dumps(dict(sha256=digest, size=len(wrong))).encode()
        self.remote.put_object(Bucket=bucket_name, Key=relative + '/originals/' + digest + '.json',
                               Body=raw, Metadata={'sha256': hashlib.sha256(raw).hexdigest()})
        shutil.rmtree(self.dashboard.data / 'skill-assets')
        with self.assertRaisesRegex(RuntimeError, 'Skill.*恢复失败'):
            self.restart()

    def test_fresh_replacement_blocks_missing_previously_confirmed_remote_record(self):
        self.assertEqual(self.upload(assets.package())['status'], 'succeeded')
        next(self.remote.root.glob('objects/**/skills/*/record.json')).unlink()
        shutil.rmtree(self.dashboard.data / 'skill-assets')
        with self.assertRaisesRegex(RuntimeError, 'Skill.*恢复失败'):
            self.restart()

    def test_restored_queue_reference_blocks_loss_of_entire_remote_skill_namespace(self):
        self.assertEqual(self.upload(assets.package())['status'], 'succeeded')
        version = self.request('/api/skills').json['versions'][0]
        root = Path(self.temp.name)
        (root / 'data').mkdir()
        source = Path(__file__).resolve().parents[2] / 'data/pilot1.jsonl'
        (root / 'data/pilot1.jsonl').write_bytes(source.read_bytes())
        save(root / 'model.local.json', dict(LCR_LLM_MODEL='local-review', LCR_LLM_TOKEN='local-only',
             LCR_LLM_BASE_URL='http://127.0.0.1:9/v1', JUDGE_MODEL='local-judge',
             JUDGE_API_KEY='local-only', JUDGE_BASE_URL='http://127.0.0.1:9/v1'))
        self.dashboard = DurableDashboard(root)
        self.client = create_app(self.dashboard).test_client()
        reply = self.request('/api/jobs', 'POST', json=dict(action='create', suite='aacr',
             dataset='pilot1.jsonl', name='persistent-selected', reviewer='dsh_skill',
             skill_mode='skill', skill_version=version['id'], evaluator='original'))
        self.assertEqual(reply.status_code, 202, reply.json)
        RunDataStorage().sync(force=True)
        shutil.rmtree(self.dashboard.data / 'ui-state')
        shutil.rmtree(self.dashboard.data / 'skill-assets')
        shutil.rmtree(next(self.remote.root.glob('objects/**/skills')))
        with patch.dict(os.environ, {'BENCH_RUN_STORAGE_MODE': 'cos'}):
            restore_run_data()
        with self.assertRaisesRegex(RuntimeError, 'Skill.*恢复失败'):
            self.restart()

    def test_native_dsh_evidence_survives_temp_cleanup_and_run_backup_with_zero_call_recovery(self):
        self.assertEqual(self.upload(assets.package())['status'], 'succeeded')
        version = self.request('/api/skills').json['versions'][0]
        with serve() as model, case(model, skill_version=version['id'], assets_root=self.temp.name) as probe:
            reply = probe.review()
            self.assertEqual(reply.returncode, 0, reply.stdout + reply.stderr)
            calls = len(model.requests)
            self.assertGreater(calls, 0)
            run = self.dashboard.data / 'runs/baseline'
            shutil.copytree(probe.run, run)
            answer = (run / 'results/local.case.json').read_bytes()
            execution = json.loads((run / 'raw/local.case.execution.json').read_text())
            command = execution['command_without_credentials']
            self.assertFalse(Path(command[command.index('/work') - 1]).parent.exists())
            records = {p.relative_to(run).as_posix(): p.read_bytes() for p in run.rglob('*')
                       if p.is_file() and p.name != '.cloud.lock'}
            self.assertTrue(any('.session-' in name for name in records))
            self.assertIn('raw/local.case.model-inputs.json', records)
            self.assertNotIn(b'local-secret', b''.join(records.values()))
            storage = RunDataStorage()
            with patch.dict(os.environ, {'BENCH_RUN_STORAGE_MODE': 'cos'}):
                synchronizer = RunDataSynchronizer(storage)
                self.fault(put='/manifests/runs/baseline.json')
                synchronizer.sync_once(force=True)
                state = self.request('/api/state').json
                self.assertEqual(state['run_storage']['status'], 'error')
                self.fault()
                synchronizer.stop()  # The same final-sync path as normal container shutdown.
                self.assertEqual(self.request('/api/state').json['run_storage']['status'], 'ready')
                shutil.rmtree(run)
                shutil.rmtree(self.dashboard.data / 'skill-assets')
                restore_run_data()
                self.restart()
            for name, raw in records.items():
                self.assertEqual((run / name).read_bytes(), raw, name)
            base = '/api/artifact?key=baseline&path='
            self.assertIn('explicit_injection', self.request(base + 'results/local.case.json').json['text'])
            self.assertIn(version['id'], self.request(base + 'run-summary.zh-CN.md').json['text'])
            shutil.rmtree(probe.run)
            shutil.copytree(run, probe.run)
            (probe.run / 'results/local.case.json').unlink()
            recovered = probe.stage('recover', '--instance', 'local.case')
            self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
            self.assertEqual((probe.run / 'results/local.case.json').read_bytes(), answer)
            self.assertEqual(len(model.requests), calls)
