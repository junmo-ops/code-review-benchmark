"""Frozen per-request DSH output limits; no external models or storage."""
import json
import os
import unittest
from unittest.mock import patch

import test_dsh_baseline as baseline
from dsh_probe import case
from reviewers.dsh_skill import runtime
from tools.dsh_local_model import serve
from webui.queue import DurableDashboard


class OutputLimitApiTests(unittest.TestCase):
    setUp = baseline.BaselineCreationTests.setUp
    tearDown = baseline.BaselineCreationTests.tearDown
    request = baseline.BaselineCreationTests.request

    def test_explicit_limit_survives_queue_restart_and_reaches_init_command(self):
        self.dashboard.submit(dict(self.request(), dsh_max_output_tokens=32768))
        restarted = DurableDashboard(self.root)
        _, queued, spec = restarted.queue.claim()
        self.assertEqual(queued['dsh_max_output_tokens'], 32768)
        self.assertEqual(spec['dsh_max_output_tokens'], 32768)
        command, _, _ = restarted.command('baseline', 'init', 'model.local.json', init=spec)
        self.assertEqual(command[command.index('--dsh-max-output-tokens') + 1], '32768')

    def test_default_remains_8192_and_invalid_values_never_enter_queue(self):
        for value in (None, True, 0, -1, 65537, '32768', []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.dashboard.submit(dict(self.request(), dsh_max_output_tokens=value))
        self.assertEqual(self.dashboard.queue.jobs(), {})
        self.dashboard.submit(self.request())
        _, _, spec = self.dashboard.queue.claim()
        self.assertEqual(spec['dsh_max_output_tokens'], 8192)

    def test_other_reviewers_cannot_receive_dsh_limits(self):
        request = dict(self.request(), reviewer='lcr', dsh_max_output_tokens=32768)
        request.pop('skill_mode')
        with self.assertRaisesRegex(ValueError, 'DSH 单次输出上限仅适用于'):
            self.dashboard.submit(request)

    def test_runtime_limit_validation_and_policy_identity(self):
        for value in (True, None, 0, 65537, '32768'):
            with self.subTest(value=value), self.assertRaises(runtime.IsolationError):
                runtime.output_limit(value)
        self.assertEqual(runtime.policy('baseline')['max_output_tokens'], 8192)
        self.assertEqual(runtime.policy('baseline', 32768)['max_output_tokens'], 32768)
        self.assertEqual(runtime.policy('skill', 32768)['max_output_tokens'], 32768)


class NativeOutputLimitTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, BENCH_DSH_RUNTIME_MODE='pod'))

    def test_default_truncation_stays_failed_and_explicit_limit_reaches_actual_model_request(self):
        def respond(body, index):
            limit = body.get('max_completion_tokens', body.get('max_tokens'))
            reason = 'length' if limit == 8192 else 'stop'
            return dict(content='{"status":"complete","comments":[]}'), reason

        with serve(respond) as model:
            with case(model) as probe:
                result = probe.review()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('max-tokens', result.stdout + result.stderr)
                self.assertIn('8192', result.stdout + result.stderr)
                self.assertFalse((probe.run / 'results/local.case.json').exists())
                self.assertEqual(len(model.requests), 1)
            with case(model, max_output_tokens=32768) as probe:
                result = probe.review()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(len(model.requests), 2)
                self.assertEqual(model.requests[-1].get('max_completion_tokens'), 32768)
                cfg = json.loads((probe.run / 'run-config.json').read_text())
                self.assertEqual(cfg['dsh_max_output_tokens'], 32768)
                self.assertEqual(cfg['dsh_policy']['max_output_tokens'], 32768)
                self.assertEqual(cfg['dsh_policy']['request_retries'], 0)
                original = (probe.run / 'results/local.case.json').read_bytes()
                self.assertEqual(probe.stage('recover', '--instance', 'local.case').returncode, 0)
                self.assertEqual((probe.run / 'results/local.case.json').read_bytes(), original)
                self.assertEqual(len(model.requests), 2)
                override = probe.stage('review', '--instance', 'local.case',
                                       '--dsh-max-output-tokens', '65536')
                self.assertNotEqual(override.returncode, 0)
                self.assertEqual(len(model.requests), 2)

    def test_provider_truncation_still_fails_with_a_larger_limit_and_never_retries(self):
        with serve(lambda body, index: (dict(content='{"status":"complete","comments":[]}'), 'length')) as model:
            with case(model, max_output_tokens=32768) as probe:
                result = probe.review()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('32768', result.stdout + result.stderr)
                self.assertFalse((probe.run / 'results/local.case.json').exists())
                self.assertEqual(len(model.requests), 1)
                path = probe.run / 'run-config.json'
                cfg = json.loads(path.read_text())
                cfg['dsh_max_output_tokens'] = 8192
                path.write_text(json.dumps(cfg))
                self.assertNotEqual(probe.review().returncode, 0)
                self.assertEqual(len(model.requests), 1)


if __name__ == '__main__':
    unittest.main()
