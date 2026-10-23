"""DSH baseline through the existing durable creation interface; no model calls."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from webui.queue import DurableDashboard
from webui.service import save


class BaselineCreationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.environment = patch.dict(os.environ, {
            'BENCH_DATA_ROOT': str(self.root), 'BENCH_CONFIG_ROOT': str(self.root),
            'BENCH_SERVER_MODE': '', 'BENCH_CASE_UPLOAD': '',
            'BENCH_AGENT_CONFIG': str(self.root / 'agent.json'),
        })
        self.environment.start()
        (self.root / 'data').mkdir()
        (self.root / 'data/pilot1.jsonl').write_bytes(
            (Path(__file__).resolve().parents[2] / 'data/pilot1.jsonl').read_bytes())
        (self.root / 'agent.json').write_bytes(
            (Path(__file__).resolve().parents[2] / 'agent.example.json').read_bytes())
        save(self.root / 'model.local.json', dict(
            LCR_LLM_MODEL='review-model', LCR_LLM_TOKEN='review-secret',
            LCR_LLM_BASE_URL='http://127.0.0.1:9/v1',
            JUDGE_MODEL='judge-model', JUDGE_API_KEY='judge-secret',
            JUDGE_BASE_URL='http://127.0.0.1:9/v1'))
        self.dashboard = DurableDashboard(self.root)

    def tearDown(self):
        self.environment.stop()
        self.tmp.cleanup()

    def request(self):
        return dict(action='create', suite='aacr', dataset='pilot1.jsonl',
                    name='baseline', reviewer='dsh_skill', skill_mode='baseline',
                    evaluator='original')

    def test_existing_tools_keep_creation_scoring_and_retry_defaults(self):
        for reviewer in ('lcr', 'cline_skill', 'server_agent'):
            for evaluator in ('original', 'martian'):
                request = dict(action='create', suite='aacr', dataset='pilot1.jsonl',
                               name=reviewer + evaluator, reviewer=reviewer, evaluator=evaluator)
                self.dashboard.submit(request)
                job, _, frozen = self.dashboard.queue.claim()
                self.assertEqual(frozen['reviewer'], reviewer)
                self.assertEqual(frozen['evaluator'], evaluator)
                self.assertEqual(frozen['max_case_retries'], 1 if reviewer == 'lcr' else 0)
                self.assertNotIn('skill_mode', frozen)
                self.dashboard.queue.update(job['id'], {'status': 'succeeded'})

    def test_baseline_persists_once_and_keeps_scoring_independent(self):
        for evaluator in ('original', 'martian'):
            request = self.request()
            request.update(name=evaluator, evaluator=evaluator)
            result = self.dashboard.submit(request)
            restarted = DurableDashboard(self.root)
            job, queued, frozen = restarted.queue.claim()
            self.assertEqual(job['id'], result['job_id'])
            self.assertEqual(frozen['reviewer'], 'dsh_skill')
            self.assertEqual(frozen['skill_mode'], 'baseline')
            self.assertEqual(frozen['evaluator'], evaluator)
            self.assertEqual(frozen['timeout_minutes'], 10)
            self.assertEqual(frozen['max_case_retries'], 0)
            self.assertEqual(queued['skill_mode'], 'baseline')
            self.assertIsNone(restarted.queue.claim())
            restarted.queue.update(job['id'], {'status': 'succeeded'})
        self.assertEqual(len(self.dashboard.queue.jobs()), 2)
        self.assertNotIn('review-secret', json.dumps(self.dashboard.queue.jobs()))

    def test_baseline_mode_is_explicit_and_not_allowed_on_other_tools(self):
        for changes in ({'skill_mode': None}, {'skill_mode': 'skill'},
                        {'skill_version': 'invented'}, {'suite': 'swe_prbench_llm',
                         'dataset': 'swe-prbench-pilot1.jsonl'},
                        {'reviewer': 'lcr'}):
            request = self.request()
            request.update(changes)
            with self.assertRaises(ValueError):
                self.dashboard.submit(request)
        self.assertEqual(self.dashboard.queue.jobs(), {})

    def test_queue_freezes_deployment_mode_instead_of_accepting_a_client_override(self):
        with patch.dict(os.environ, BENCH_DSH_RUNTIME_MODE='pod'):
            request = dict(self.request(), dsh_runtime_mode='sandbox')
            self.dashboard.submit(request)
            _, _, spec = self.dashboard.queue.claim()
            self.assertEqual(spec['dsh_runtime_mode'], 'pod')
        command, _, _ = self.dashboard.command('baseline', 'init', 'model.local.json', init=spec)
        self.assertEqual(command[command.index('--dsh-runtime-mode') + 1], 'pod')
