"""Read-model regressions: pagination, portable keys, freshness and bounded work."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from application.files import save
from webui.service import Dashboard


class RunQueryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, BENCH_DATA_ROOT=str(self.root),
                         BENCH_CONFIG_ROOT=str(self.root / 'config'), BENCH_CASE_UPLOAD='0')
        env.start()
        self.addCleanup(env.stop)
        self.app = Dashboard(self.root)
        self.addCleanup(self.app.shutdown)
        self.seed('one', '2026-01-01')

    def seed(self, name, created):
        save(self.root / 'runs/ccrab' / name / 'run-config.json',
             dict(instance_id='case', review_model='model-a', created_at=created))

    def test_lists_do_not_inspect_artifacts_or_verify_source_identity(self):
        with patch.object(self.app, 'artifacts', side_effect=AssertionError('expensive scan')), \
                patch.object(self.app, 'compatibility', side_effect=AssertionError('source hashing')):
            result = self.app.queries.page()
            self.assertEqual(result['items'][0]['key'], 'ccrab/one')
            self.assertEqual(result['items'][0]['cases'], [])
            self.assertEqual(self.app.snapshot(compact=True)['runs'], [])

    def test_cached_reads_are_reused_and_atomic_status_updates_invalidate_them(self):
        with patch.object(self.app, '_ccrab_metrics', wraps=self.app._ccrab_metrics) as metrics:
            self.assertEqual(self.app.queries.page()['items'][0]['completed'], 0)
            self.app.queries.page()
            self.assertEqual(metrics.call_count, 1)
            save(self.root / 'runs/ccrab/one/status.json',
                 dict(status='complete', review_executed=True, repair_executed=True, test_pass_rate=1))
            value = self.app.queries.page()['items'][0]
            self.assertEqual(value['completed'], 1)
            self.assertEqual(value['metrics'], {'test_pass_rate': 1})
            self.assertEqual(metrics.call_count, 2)

    def test_pagination_search_labels_and_group_exclusion(self):
        self.seed('two', '2026-01-02')
        self.seed('group', '2026-01-03')
        save(self.root / 'runs/ccrab/group/comparison-protocol.json', {})
        save(self.app.state / 'display-names.json', {'ccrab/two': '中文批次'})
        first = self.app.queries.page(1, 1)
        self.assertEqual(first['total'], 2)
        self.assertEqual(first['items'][0]['display_name'], '中文批次')
        self.assertEqual(self.app.queries.page(2, 1)['items'][0]['key'], 'ccrab/one')
        self.assertEqual(self.app.queries.page(99, 1)['page'], 2)
        self.assertEqual(self.app.queries.page(search='中文')['total'], 1)
        self.assertEqual(self.app.queries.page(suite='aacr')['total'], 0)
        for size in [0, 101, True]:
            with self.subTest(size=size), self.assertRaises(ValueError):
                self.app.queries.page(page_size=size)

    def test_cache_is_bounded_and_detail_reads_are_always_fresh(self):
        self.app.queries.maximum = 2
        self.seed('two', '2026-01-02')
        self.seed('three', '2026-01-03')
        self.app.queries.page()
        self.assertLessEqual(len(self.app.queries.cache), 2)
        with patch.object(self.app, 'describe', wraps=self.app.describe) as describe:
            self.app.queries.detail('ccrab/one')
            self.app.queries.detail('ccrab/one')
            self.assertEqual(describe.call_count, 2)
        with self.assertRaises(ValueError):
            self.app.queries.detail('../outside')

    def test_unreadable_run_does_not_hide_other_runs_and_artifacts_use_slashes(self):
        self.app.jobs['legacy'] = dict(id='legacy', status='interrupted', created_at='2026-01-01')
        path = self.root / 'runs/ccrab/broken/run-config.json'
        path.parent.mkdir()
        path.write_text('{', encoding='utf-8')
        self.assertEqual(self.app.queries.page()['total'], 2)
        save(self.root / 'runs/ccrab/one/diagnostics/result.json', {'ok': True})
        paths = {entry['path'] for entry in self.app.artifacts('ccrab/one')}
        self.assertIn('diagnostics/result.json', paths)


if __name__ == '__main__':
    unittest.main()
