"""Exercise the production worker without launching model commands."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runner.locking import acquire, release
from webui import case_import
from webui.queue import DurableDashboard
from webui.worker import run_worker, _execute_claimed


class WorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, BENCH_DATA_ROOT=str(self.root),
                         BENCH_CONFIG_ROOT=str(self.root / 'config'))
        env.start()
        self.addCleanup(env.stop)
        self.app = DurableDashboard(self.root)
        self.addCleanup(self.app.shutdown)
        self.job = dict(id='job', key='batch', status='queued', profile='environment',
                        profile_fingerprint='frozen', created_at='2026-01-01')
        self.app.queue.insert(self.job, {'action': 'workflow'}, None)

    def test_worker_claims_executes_and_clears_heartbeat_and_lock(self):
        def work(identifier, request, spec):
            self.assertEqual(self.app.queue.jobs()[identifier]['status'], 'running')
            self.app.update(identifier, status='succeeded')
            self.app.stopping = True
        with patch.object(self.app, 'fingerprint', return_value='frozen'), \
                patch.object(self.app, 'work', side_effect=work) as execute:
            run_worker(self.app, install_signals=False)
        execute.assert_called_once()
        self.assertEqual(self.app.queue.jobs()['job']['status'], 'succeeded')
        self.assertEqual(self.app.queue.meta('heartbeat'), 0)
        self.assertIsNone(self.app.active)
        with (self.app.state / 'worker.lock').open('a+b') as lock:
            acquire(lock)
            release(lock)

    def test_second_worker_cannot_recover_a_live_workers_job(self):
        self.app.queue.claim()
        with (self.app.state / 'worker.lock').open('a+b') as lock:
            acquire(lock)
            try:
                with self.assertRaises(RuntimeError):
                    run_worker(self.app, install_signals=False)
                self.assertEqual(self.app.queue.jobs()['job']['status'], 'running')
            finally:
                release(lock)

    def test_configuration_drift_fails_before_command_execution(self):
        claimed = self.app.queue.claim()
        with patch.object(self.app, 'fingerprint', return_value='changed'), patch.object(self.app, 'work') as work:
            _execute_claimed(self.app, claimed)
        work.assert_not_called()
        self.assertEqual(self.app.queue.jobs()['job']['status'], 'failed')

    def test_closing_other_dashboard_does_not_stop_the_import_owner(self):
        case_import.start(self.app)
        other = DurableDashboard(self.root)
        other.shutdown()
        key = str(self.app.state.resolve())
        self.assertTrue(case_import._workers[key].is_alive())
        self.app.shutdown()
        self.assertNotIn(key, case_import._workers)
