"""Admission policy regressions after separating submission and execution."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from application.files import save
from webui.queue import DurableDashboard


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, BENCH_DATA_ROOT=str(self.root),
                         BENCH_CONFIG_ROOT=str(self.root / 'config'), BENCH_SERVER_MODE='',
                         BENCH_ENABLED_SUITES='', BENCH_MODEL_STORAGE_MODE='local')
        env.start()
        self.addCleanup(env.stop)
        self.app = DurableDashboard(self.root)
        self.addCleanup(self.app.shutdown)
        save(self.app.configs / 'model.local.json', dict(LCR_LLM_MODEL='test', LCR_LLM_TOKEN='fake',
             LCR_LLM_BASE_URL='http://127.0.0.1:9/v1', JUDGE_MODEL='test', JUDGE_API_KEY='fake'))
        catalog = patch.object(self.app, 'catalog', return_value=[
            {'id': suite, 'datasets': [{'id': 'fixture.jsonl'}]} for suite in ['aacr', 'mcr', 'codefuse_cr']])
        catalog.start()
        self.addCleanup(catalog.stop)
        self.request = dict(action='create', name='sample', suite='aacr', dataset='fixture.jsonl')

    def test_invalid_budgets_and_flags_do_not_leave_queued_jobs(self):
        invalid = [('timeout_minutes', 0), ('max_tokens', True), ('case_jobs', 17),
                   ('max_case_retries', 3), ('start_after_create', 1)]
        for name, value in invalid:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.app.submit(dict(self.request, **{name: value}))
        self.assertEqual(self.app.queue.jobs(), {})

    def test_suite_and_reviewer_capabilities_are_checked_before_queueing(self):
        invalid = [dict(suite='mcr', reviewer='server_agent'),
                   dict(suite='mcr', case_jobs=2), dict(suite='codefuse_cr', evaluator='original'),
                   dict(reviewer='cline_skill'), dict(dataset='../outside.jsonl')]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.app.submit(dict(self.request, **changes))
        self.assertEqual(self.app.queue.jobs(), {})

    def test_valid_submission_preserves_execution_limits_and_resource_reference(self):
        result = self.app.submit(dict(self.request, case_jobs=4, timeout_minutes=12,
                                      max_case_retries=2, start_after_create=True))
        job, request, spec = self.app.queue.claim()
        self.assertEqual(job['id'], result['job_id'])
        self.assertEqual(job['resource_dataset'], 'fixture.jsonl')
        self.assertEqual((spec['case_jobs'], spec['timeout_minutes'], spec['max_case_retries']), (4, 12, 2))
        self.assertTrue(request['start_after_create'])

    def test_failed_admission_removes_only_the_new_frozen_profile(self):
        before = {path.name for path in self.app.configs.iterdir()}
        with patch.object(self.app, 'enqueue', side_effect=ValueError('injected admission failure')):
            with self.assertRaisesRegex(ValueError, 'injected admission failure'):
                self.app.submit(dict(self.request, review_profile='model.local.json',
                                     judge_profile='model.local.json'))
        self.assertEqual({path.name for path in self.app.configs.iterdir()}, before)
        self.assertEqual(self.app.queue.jobs(), {})
