"""Offline fault injection against the real AACR matching and scoring pipeline."""
import argparse
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from openai import APIStatusError

from runner import cloud
from runner.paths import PROJECT_ROOT as ROOT
from runner.storage import save
from reviewers.lcr import adapter
from suites import registry
from suites.aacr import evaluator, report
from suites.aacr.evaluation_resume import scoring_budget


def api_error(status):
    response = httpx.Response(status, request=httpx.Request('POST', 'https://offline.invalid/chat/completions'))
    return APIStatusError('offline fault injection', response=response, body=None)


class Client:
    calls = 0
    failures = {}
    answers = {}

    def __init__(self, **kwargs):
        assert kwargs['timeout'] == 180 and kwargs['max_retries'] == 0
        self.chat = SimpleNamespace(completions=self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def create(self, **kwargs):
        Client.calls += 1
        status = Client.failures.get(Client.calls)
        if status:
            raise api_error(status)
        usage = SimpleNamespace(model_dump=lambda: dict(prompt_tokens=10, completion_tokens=1, total_tokens=11))
        choice = SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=Client.answers.get(Client.calls, 'yes')))
        return SimpleNamespace(choices=[choice], usage=usage)


class AACRScoringResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run = self.root / 'runs/run'
        self.run.mkdir(parents=True)
        self.env = patch.dict(os.environ, JUDGE_BASE_URL='https://offline.invalid/v1',
                              JUDGE_MODEL='offline', JUDGE_API_KEY='offline-key', JUDGE_USE_MOCK='false')
        self.env.start()
        rows = [json.loads(line) for line in (ROOT / 'data/pilot5.jsonl').read_text().splitlines()][:2]
        for row in rows:
            row['reference_comments'] = row['reference_comments'][:1]
        source = self.root / 'dataset.jsonl'
        source.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        args = argparse.Namespace(suite='aacr', reviewer='lcr', evaluator='original',
                                  dataset=str(source), model='offline', repos_root=None, timeout_minutes=10)
        cloud.init_run(args, self.run)
        self.config = json.loads((self.run / 'run-config.json').read_text())
        self.cases = registry.get_suite('aacr').load_cases(self.run / 'dataset.jsonl')
        for case, row in zip(self.cases, rows):
            comments = [dict(filePath=c['path'], startLine=c['start_line'], endLine=c['end_line'], description=c['text'])
                        for c in row['reference_comments']]
            save(self.run / 'results' / (case.safe_id + '.json'), adapter.convert(case, dict(status='success', comments=comments)))
        self.client = patch('openai.AsyncOpenAI', Client)
        self.client.start()
        self.sleep = patch('suites.aacr.judge_transport.asyncio.sleep', new_callable=AsyncMock).start()
        patch.object(evaluator, 'load_judge_config', return_value=None).start()
        patch.object(evaluator, 'stage_report', side_effect=lambda run, cfg: asyncio.run(report.generate_report(run, offline=True))).start()
        # Any accidental real model or COS connection fails the test.
        patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden')).start()
        import judge
        self.mock_mode = patch.object(judge, 'USE_MOCK_LLM', False)
        self.mock_mode.start()
        Client.calls, Client.failures = 0, {}
        Client.answers = {}

    def tearDown(self):
        patch.stopall()
        self.env.stop()
        self.temp.cleanup()

    def evaluate(self):
        evaluator.stage_eval(None, self.run, self.config)

    def fail_third_round(self):
        Client.failures = {6: 504, 7: 504, 8: 504}
        with self.assertRaisesRegex(RuntimeError, '进度已保存'):
            self.evaluate()
        rounds = sorted((self.run / 'metrics').glob('*round_*.json'))
        self.assertEqual(len(rounds), 2)
        self.assertFalse((self.run / 'metrics/metrics_ocr_average.json').exists())
        self.assertFalse((self.run / 'report.zh-CN.md').exists())
        return {path: path.read_bytes() for path in rounds}

    def assert_finished(self, preserved):
        self.assertTrue(all(path.read_bytes() == value for path, value in preserved.items()))
        audit = json.loads((self.run / 'judge-audit.json').read_text())
        self.assertEqual(audit['status'], 'complete')
        self.assertEqual(audit['judge_usage_only']['total_tokens'], 66)
        metrics = json.loads((self.run / 'metrics/metrics_ocr_average.json').read_text())
        self.assertEqual(metrics['eval_rounds'], 3)
        self.assertEqual(metrics['summary']['evaluated_instances'], 2)
        self.assertEqual(metrics['summary']['semantic_f1'], 1)
        self.assertIn('耗时与用量', (self.run / 'report.zh-CN.md').read_text())

    def test_504_retries_current_request_without_repeating_successes(self):
        Client.failures = {2: 504}
        self.evaluate()
        self.assertEqual(Client.calls, 7)
        self.sleep.assert_awaited_once_with(10)
        self.assert_finished({})
        with self.assertRaisesRegex(ValueError, '评分已完成'):
            self.evaluate()
        self.assertEqual(Client.calls, 7)

    def test_resume_preserves_two_rounds_and_reuses_third_round_success(self):
        preserved = self.fail_third_round()
        self.assertEqual([call.args[0] for call in self.sleep.await_args_list], [10, 30])
        self.evaluate()
        self.assertEqual(Client.calls, 9)
        self.assert_finished(preserved)

    def test_old_server_records_resume_without_rerunning_completed_rounds(self):
        preserved = self.fail_third_round()
        records = json.loads((self.run / 'judge-requests.json').read_text())
        legacy = [{key: row[key] for key in ('prompt_sha256', 'status', 'answer', 'usage', 'error_type') if key in row}
                  for row in records if row['status'] == 'success']
        legacy.append({key: records[-1][key] for key in ('prompt_sha256', 'status', 'error_type')})
        save(self.run / 'judge-requests.json', legacy)
        (self.run / 'judge-checkpoints.json').unlink()
        (self.run / 'evaluation-resume.json').unlink()
        old_identity = self.config['suite']['identity']
        for name in registry.AACR_SCORING_ADDITIONS:
            del old_identity['files'][name]
        old_identity['files']['backend/src/suites/aacr/evaluator.py'] = '72a75921d544b7ecbfd35db16c3650223b9838863adc7c9d8b79da056c76de0a'
        save(self.run / 'run-config.json', self.config)
        frozen = json.loads((self.run / 'evaluation-config.json').read_text())
        frozen['suite_identity'] = old_identity
        save(self.run / 'evaluation-config.json', frozen)
        Client.calls, Client.failures = 0, {}
        suite = registry.resolve_run_suite(self.config)
        from scoring.registry import execute
        with patch.object(suite, '_execute', side_effect=lambda stage, run: evaluator.stage_eval(None, run, self.config)):
            execute('eval', suite, self.cases, self.run, self.config)
        self.assertEqual(Client.calls, 1)
        self.assert_finished(preserved)

    def test_401_is_not_retried(self):
        Client.failures = {1: 401}
        with self.assertRaises(RuntimeError):
            self.evaluate()
        self.assertEqual(Client.calls, 1)
        self.sleep.assert_not_awaited()

    def test_empty_response_retries_and_keeps_returned_token_usage(self):
        Client.answers = {2: None}
        self.evaluate()
        self.assertEqual(Client.calls, 7)
        audit = json.loads((self.run / 'judge-audit.json').read_text())
        self.assertEqual(audit['status'], 'complete')
        self.assertEqual(audit['judge_usage_only']['total_tokens'], 77)
        self.assertEqual(audit['requests'][1]['status'], 'failed')

    def test_rounds_keep_independent_judgments_for_identical_prompts(self):
        Client.answers = {3: 'no', 4: 'no'}
        self.evaluate()
        self.assertEqual(Client.calls, 6)
        average = json.loads((self.run / 'metrics/metrics_ocr_average.json').read_text())
        self.assertEqual(average['summary']['semantic_f1'], 0.6667)

    def test_changed_answer_cannot_resume(self):
        self.fail_third_round()
        path = self.run / 'results' / (self.cases[0].safe_id + '.json')
        answer = json.loads(path.read_text())
        answer['review']['comments'][0]['content'] += 'changed'
        save(path, answer)
        with self.assertRaisesRegex(ValueError, '答卷或数据已变化'):
            self.evaluate()
        self.assertEqual(Client.calls, 8)

    def test_completed_round_without_request_evidence_stays_unchanged(self):
        preserved = self.fail_third_round()
        save(self.run / 'judge-requests.json', [])
        save(self.run / 'judge-checkpoints.json', {})
        with self.assertRaises(RuntimeError):
            self.evaluate()
        self.assertEqual(Client.calls, 8)
        self.assertTrue(all(path.read_bytes() == value for path, value in preserved.items()))

    def test_success_saved_before_checkpoint_index_is_recovered(self):
        preserved = self.fail_third_round()
        (self.run / 'judge-checkpoints.json').unlink()
        self.evaluate()
        self.assertEqual(Client.calls, 9)
        self.assert_finished(preserved)

    def test_checkpoint_write_error_stops_instead_of_becoming_a_negative_judgment(self):
        def write(path, value):
            if path.name == 'judge-checkpoints.json':
                raise OSError('offline simulated disk error')
            save(path, value)
        with patch('suites.aacr.judge_transport.save', side_effect=write):
            with self.assertRaises(RuntimeError):
                self.evaluate()
        self.assertEqual(Client.calls, 1)
        self.assertFalse(list((self.run / 'metrics').glob('*round_*.json')))
        self.evaluate()
        self.assertEqual(Client.calls, 6)
        self.assert_finished({})

    def test_empty_first_eval_does_not_freeze_an_empty_scope(self):
        for path in (self.run / 'results').glob('*.json'):
            path.unlink()
        with self.assertRaisesRegex(ValueError, '没有可评分'):
            self.evaluate()
        self.assertFalse((self.run / 'evaluation-scope.json').exists())
        self.assertEqual(Client.calls, 0)

    def test_resume_keeps_original_scope_when_another_answer_becomes_complete(self):
        path = self.run / 'results' / (self.cases[1].safe_id + '.json')
        saved = path.read_bytes()
        path.unlink()
        Client.failures = {3: 504, 4: 504, 5: 504}
        with self.assertRaises(RuntimeError):
            self.evaluate()
        path.write_bytes(saved)
        self.evaluate()
        scope = json.loads((self.run / 'evaluation-scope.json').read_text())
        self.assertEqual(scope['included'], [self.cases[0].instance_id])
        self.assertEqual(Client.calls, 6)

    def test_scoring_budget_is_used_for_both_lcr_and_server_agent(self):
        from webui.service import Dashboard
        dashboard = object.__new__(Dashboard)
        dashboard.data = self.root
        dashboard.root = ROOT
        expected = scoring_budget(self.run, self.config)
        self.assertEqual(dashboard._scoring_budget(self.config, self.run), expected)
        _, _, limit = dashboard._agent_command('eval', 'run', {}, self.config, None, None)
        self.assertEqual(limit, expected)
        _, _, limit = dashboard._review_arguments('run', 'eval', 'run', self.config, None, None, False)
        self.assertEqual(limit, expected)

    def test_legacy_frozen_suite_accepts_only_pinned_scoring_changes(self):
        current = registry.get_suite('aacr').identity()
        old = copy.deepcopy(current)
        for name in registry.AACR_SCORING_ADDITIONS:
            del old['files'][name]
        old['files']['backend/src/suites/aacr/evaluator.py'] = '72a75921d544b7ecbfd35db16c3650223b9838863adc7c9d8b79da056c76de0a'
        self.assertTrue(registry.compatible_frozen_identity(old, current, 'aacr'))
        changed = copy.deepcopy(current)
        changed['files']['backend/src/suites/aacr/judge_transport.py'] = hashlib.sha256(b'changed').hexdigest()
        self.assertFalse(registry.compatible_frozen_identity(old, changed, 'aacr'))
        self.assertFalse(registry.compatible_frozen_identity(old, current, 'swe_prbench'))

    def test_budget_includes_all_rounds_and_retry_delays(self):
        self.assertGreaterEqual(scoring_budget(self.run, self.config), 4380)


if __name__ == '__main__':
    unittest.main()
