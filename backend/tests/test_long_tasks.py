"""Offline recovery regressions: no model or COS network calls."""
import io
import json
import os
from pathlib import Path
import tempfile
import time
import threading
import subprocess
import unittest
from unittest.mock import patch

from deployment.run_storage import RunDataStorage, _trim_jsonl_snapshot
from deployment.session_checkpoint import sanitize_checkpoint
from runner.evidence import evidence_files
from runner.cloud import exclusive, _pending_review_items, _clear_interrupted_workspaces
from webui.resume import prepare_resume
from webui.queue import DurableDashboard
from webui.service import Dashboard
from types import SimpleNamespace


class LongTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_traversal_prunes_before_entering_workspaces(self):
        for name in ['raw/a.json', 'workspaces/repo/.git/objects/a.json', 'repair-workspace/a.json']:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}')
        visited = []
        original = os.scandir
        def scan(path):
            visited.append(str(path))
            return original(path)
        with patch('os.scandir', side_effect=scan):
            files = list(evidence_files(self.root))
        self.assertEqual(files, [self.root / 'raw/a.json'])
        self.assertFalse(any('workspaces' in name or 'repair-workspace' in name for name in visited))

    def resume_app(self):
        return SimpleNamespace(
            config=lambda _: {'reviewer': 'lcr'}, run_dir=lambda _: self.root,
            queue=SimpleNamespace(meta=lambda _: time.time()),
            jobs={'old': {'key': 'aacr-one', 'created_at': '1', 'status': 'interrupted'}})

    def test_resume_requires_confirmation_and_exclusive_lock(self):
        app = self.resume_app()
        with self.assertRaises(ValueError):
            prepare_resume(app, {'key': 'aacr-one'})
        request = {'key': 'aacr-one', 'confirmed': True}
        with exclusive(self.root):
            with self.assertRaises(ValueError):
                prepare_resume(app, request)
        prepared = prepare_resume(app, request)
        self.assertEqual(prepared['action'], 'workflow')
        self.assertTrue(prepared['retry'])
        app.jobs['old']['status'] = 'cancelled'
        self.assertEqual(prepare_resume(app, request)['action'], 'workflow')
        app.jobs['old']['status'] = 'queued'
        with self.assertRaises(ValueError):
            prepare_resume(app, request)

    def test_complete_answers_are_skipped_when_retry_is_explicit(self):
        done = SimpleNamespace(instance_id='done', safe_id='done')
        pending = SimpleNamespace(instance_id='pending', safe_id='pending')
        args = SimpleNamespace(retry=True)
        rows = [{'instance_id': 'done', 'status': 'complete'},
                {'instance_id': 'pending', 'status': 'unfinished_check_required'}]
        with patch('runner.cloud.adapter.validate_answer') as validate:
            result = _pending_review_items(args, {}, [done, pending], rows, self.root)
        self.assertEqual(result, [pending])
        validate.assert_called_once()

    def test_jsonl_checkpoint_preserves_completed_events_and_redacts_keys(self):
        path = self.root / 'session.jsonl'
        records = [{'type': 'session_start', 'apiKey': 'private-key'},
                   {'type': 'review_item_done', 'fingerprint': 'one', 'comments': []}]
        path.write_bytes((''.join(json.dumps(r) + '\n' for r in records) + '{"incomplete":').encode())
        _trim_jsonl_snapshot(path)
        sanitize_checkpoint(path)
        restored = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(restored[1], records[1])
        self.assertEqual(restored[0]['apiKey'], '[REDACTED]')

    def test_cos_roundtrip_restores_session_to_lcr_home(self):
        storage = RunDataStorage.__new__(RunDataStorage)
        storage.data = self.root
        storage.uploaded = set()
        storage.observed = {}
        storage.stable_seconds = 15
        storage.preserved_files = 0
        storage.object_prefix = 'objects/'
        objects = {}
        storage._put_object = lambda path, digest: objects.update({storage._object_key(digest): path.read_bytes()})
        session = self.root / 'home/.leancodereview/sessions/date/id.jsonl'
        session.parent.mkdir(parents=True)
        session.write_text('{"type":"review_item_done","filePath":"a.ts"}\n')
        record = storage._sync_file(session, None, False)
        self.assertIsNotNone(record)  # Active JSONL does not wait for 15s stability.
        payload = objects[storage._object_key(record['sha256'])]
        session.unlink()
        class Body(io.BytesIO):
            def iter_chunks(self, chunk_size):
                yield self.read()
        storage.client = SimpleNamespace(get_object=lambda **_: {'Body': Body(payload)})
        storage.bucket = 'fake'
        destination = storage._local_group('state/lcr-sessions')
        self.assertTrue(storage._safe_group('state/lcr-sessions'))
        storage._restore_file(destination, 'date/id.jsonl', record)
        self.assertEqual(session.read_bytes(), payload)

    def test_restart_and_explicit_resume_enqueue_once_and_unblock(self):
        with patch.dict(os.environ, {'BENCH_DATA_ROOT': str(self.root / 'data'),
                                    'BENCH_CONFIG_ROOT': str(self.root / 'models'),
                                    'BENCH_RUN_STORAGE_MODE': 'local'}):
            app = DurableDashboard(self.root)
            old = {'id': 'old', 'key': 'aacr-one', 'status': 'running',
                   'created_at': '1', 'profile': 'model.local.json', 'step': 'review'}
            app.queue.insert(old, {}, None)
            app.queue.recover()
            app.queue.meta('heartbeat', time.time())
            app.run_dir('aacr-one').mkdir(parents=True)
            def submit(instance, request):
                self.assertEqual(request['action'], 'workflow')
                self.assertTrue(request['retry'])
                new = dict(old, id='new', created_at='2', status='queued')
                instance.queue.insert(new, request, None)
                return {'job_id': 'new'}
            with patch.object(app, 'config', return_value={'reviewer': 'lcr'}), \
                 patch.object(Dashboard, 'submit', autospec=True, side_effect=submit):
                request = {'action': 'resume', 'key': 'aacr-one', 'confirmed': True}
                result = app.submit(request)
                self.assertEqual(result['job_id'], 'new')
                self.assertFalse(app.queue.meta('recovery_required'))
                with self.assertRaises(ValueError):
                    app.submit(request)
            restarted = DurableDashboard(self.root)
            self.assertEqual(restarted.queue.jobs()['old']['status'], 'interrupted')
            self.assertEqual(restarted.queue.jobs()['new']['status'], 'queued')

    def test_restored_completed_file_is_selected_for_first_resume_attempt(self):
        from reviewers.lcr import adapter
        item = SimpleNamespace(safe_id='one', instance_id='one', base_commit='a' * 40, head_commit='b' * 40)
        session_id = '12345678-1234-1234-1234-123456789012'
        cwd = str(self.root / 'workspaces/one')
        raw = self.root / 'raw'
        raw.mkdir()
        (raw / 'one.execution.json').write_text(json.dumps({
            'started_at': '2026-10-13T00:00:00Z',
            'command_without_credentials': ['lcr', '--repo', cwd]}))
        session = self.root / '.leancodereview/sessions/date' / (session_id + '.jsonl')
        session.parent.mkdir(parents=True)
        header = {'type': 'session_start', 'sessionId': session_id,
                  'timestamp': '2026-10-13T00:00:01Z', 'cwd': cwd,
                  'diffFrom': item.base_commit, 'diffTo': item.head_commit}
        done = {'type': 'review_item_done', 'sessionId': session_id,
                'fingerprint': 'file-a', 'filePath': 'a.ts'}
        session.write_text(json.dumps(header) + '\n' + json.dumps(done) + '\n')
        with patch('pathlib.Path.home', return_value=self.root):
            self.assertEqual(adapter._initial_resume_session(item, self.root), session_id)
            with patch.object(adapter, 'REPOS_ROOT', None), \
                 patch.object(adapter, '_skip_or_archive_existing_answer', return_value=False), \
                 patch.object(adapter, 'cancellation_requested', return_value=False), \
                 patch.object(adapter, '_run_case_attempt', return_value={}) as attempt:
                self.assertIsNone(adapter.review_case(item, self.root, False, threading.Event()))
                self.assertEqual(attempt.call_args.args[-1], session_id)

    def test_interrupted_workspace_is_removed_only_for_pending_cases(self):
        pending = SimpleNamespace(repo='owner/repo', safe_id='pending', instance_id='pending')
        repo = self.root / 'owner__repo'
        repo.mkdir()
        subprocess.run(['git', '-C', str(repo), 'init', '--quiet'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(repo), '-c', 'user.name=offline', '-c', 'user.email=offline@example.invalid',
                        'commit', '--quiet', '--allow-empty', '-m', 'fixture'], check=True, capture_output=True)
        workspace = self.root / 'workspaces/pending'
        subprocess.run(['git', '-C', str(repo), 'worktree', 'add', '--detach', str(workspace)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(repo), 'worktree', 'lock', str(workspace)], check=True, capture_output=True)
        complete = self.root / 'workspaces/complete'
        complete.mkdir()
        (complete / 'code.ts').write_text('code')
        # Verify the real locked Git registration, not just shutil.rmtree.
        _clear_interrupted_workspaces([pending], {'repos_root': str(self.root)}, self.root)
        self.assertFalse((self.root / 'workspaces/pending').exists())
        self.assertTrue((self.root / 'workspaces/complete/code.ts').exists())
        listing = subprocess.check_output(['git', '-C', str(repo), 'worktree', 'list', '--porcelain']).decode()
        self.assertNotIn(str(workspace.resolve()), listing)

    def test_service_shutdown_is_interrupted_but_user_stop_stays_cancelled(self):
        with patch.dict(os.environ, {'BENCH_DATA_ROOT': str(self.root / 'data'),
                                    'BENCH_CONFIG_ROOT': str(self.root / 'models')}):
            app = DurableDashboard(self.root)
            job = {'id': 'one', 'key': 'aacr-one', 'status': 'running'}
            app.queue.insert(job, {}, None)
            app.stopping = True
            app.update('one', status='cancelled')
            self.assertEqual(app.queue.jobs()['one']['status'], 'interrupted')
            self.assertTrue(app.queue.meta('recovery_required'))
            app.queue.insert(dict(job, id='two', key='aacr-two'), {}, None)
            app.queue.stop('two')
            app.update('two', status='cancelled')
            self.assertEqual(app.queue.jobs()['two']['status'], 'cancelled')

    def test_corrupt_session_does_not_block_answer_backup(self):
        storage = RunDataStorage.__new__(RunDataStorage)
        storage.data = self.root
        storage.observed = {}
        storage.stable_seconds = 15
        storage._put_object = lambda *args: None
        session = self.root / 'home/.leancodereview/sessions/bad.jsonl'
        session.parent.mkdir(parents=True)
        session.write_text('{broken}\n')
        self.assertIsNone(storage._sync_file(session, None, True))
        self.assertEqual(storage.checkpoint_errors, 1)
        answer = self.root / 'answer.json'
        answer.write_text('{}')
        self.assertIsNotNone(storage._sync_file(answer, None, True))
