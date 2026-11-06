"""Crash consistency of the persisted recovery barrier; no external services."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from webui.queue import Queue


class RecoveryTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'queue.sqlite3'
        self.queue = Queue(self.path)
        for identifier, status in [('old', 'running'), ('next', 'queued')]:
            self.queue.insert(dict(id=identifier, key=identifier, status=status), {}, None)

    def test_failure_writing_barrier_rolls_back_interrupted_jobs(self):
        with self.queue.connect() as db:
            db.executescript("""CREATE TRIGGER fail_recovery BEFORE INSERT ON meta
                WHEN NEW.key = 'recovery_required'
                BEGIN SELECT RAISE(ABORT, 'injected barrier write failure'); END;""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.queue.recover()
        restarted = Queue(self.path)
        self.assertEqual(restarted.jobs()['old']['status'], 'running')
        with restarted.connect() as db:
            db.execute('DROP TRIGGER fail_recovery')
        restarted.recover()
        self.assertEqual(restarted.jobs()['old']['status'], 'interrupted')
        self.assertTrue(restarted.meta('recovery_required'))
        self.assertIsNone(restarted.claim())

    def test_restart_preserves_barrier_until_explicit_acknowledgement(self):
        self.queue.recover()
        restarted = Queue(self.path)
        restarted.recover()
        self.assertIsNone(restarted.claim())
        restarted.acknowledge_recovery('test-operator')
        claimed = restarted.claim()
        self.assertEqual(claimed[0]['id'], 'next')
        self.assertIsNone(restarted.claim())

    def test_claim_checks_barrier_inside_queue_transaction(self):
        self.queue.meta('recovery_required', True)
        self.assertIsNone(self.queue.claim())
        self.assertEqual(self.queue.jobs()['next']['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
