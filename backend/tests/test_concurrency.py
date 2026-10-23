"""Verify case concurrency from workbench creation through the LCR scheduler."""
import argparse
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from runner import cloud
from reviewers.lcr import adapter
from webui.service import Dashboard, save
from regression_fixtures import seed_datasets, simulated_lcr


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        seed_datasets(self.root)
        simulated_lcr(self)
        self.app = Dashboard(self.root)
        save(self.root/'model.local.json', {'LCR_LLM_MODEL': 'test'})

    def tearDown(self):
        self.app.shutdown()
        self.tmp.cleanup()

    def test_create_command_freeze_and_lcr_invocation(self):
        request = dict(action='create', name='parallel', suite='aacr',
                       dataset='pilot1.jsonl', case_jobs=4)
        with patch.object(self.app, 'enqueue', side_effect=lambda job, req, spec: spec):
            spec = self.app.submit(request)
        command, _, _ = self.app.command('parallel', 'init', 'model.local.json', init=spec)
        self.assertEqual(command[command.index('--case-jobs')+1], '4')
        run = self.root/'run'
        run.mkdir()
        args = argparse.Namespace(dataset=str(cloud.ROOT/'data/pilot1.jsonl'),
                                  model='test', repos_root=None, timeout_minutes=10,
                                  case_jobs=spec['case_jobs'])
        cloud.init_run(args, run)
        cfg = json.loads((run/'run-config.json').read_text())
        self.assertEqual(cfg['case_jobs'], 4)
        self.assertEqual(cfg['concurrency'], 1)
        item = cloud.checked_instances(run/'dataset.jsonl')[0]
        with patch.object(adapter, 'FILE_CONCURRENCY', 1), patch.object(adapter, 'CASE_JOBS', 1), \
             patch.object(adapter, 'LCR_TIMEOUT_MINUTES', 10), patch.dict(adapter.os.environ), \
             patch.object(adapter.shutil, 'which', return_value='lcr'), \
             patch.object(adapter, 'process_timeout', return_value=720), \
             patch.object(adapter, 'capture_process', return_value=SimpleNamespace(returncode=0, stdout='')) as capture:
            adapter.configure(cfg, run)
            adapter.run_lcr(item, False, run, diagnostic=True)
            invoked = capture.call_args.args[0]
            self.assertEqual(invoked[invoked.index('--concurrency')+1], '1')
            self.assertEqual(capture.call_args.kwargs['case_jobs'], 4)
        target = self.app.run_dir('parallel')
        target.mkdir(parents=True)
        save(target/'run-config.json', cfg)
        (target/'dataset.jsonl').write_bytes((run/'dataset.jsonl').read_bytes())
        self.assertEqual(self.app.describe('parallel')['concurrency'], 4)

    def test_invalid_values_are_rejected_before_enqueue(self):
        with patch.object(self.app, 'enqueue') as enqueue:
            for value in [0, 17, True, 1.5, '4', None]:
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, '并发'):
                    self.app.submit(dict(action='create', name='bad', suite='aacr',
                                         dataset='pilot1.jsonl', case_jobs=value))
            enqueue.assert_not_called()

    def test_default_and_legacy_config_remain_serial(self):
        with patch.object(self.app, 'enqueue', side_effect=lambda job, req, spec: spec):
            spec = self.app.submit(dict(action='create', name='serial', suite='aacr', dataset='pilot1.jsonl'))
        self.assertEqual(spec['case_jobs'], 1)
        with patch.object(adapter, 'FILE_CONCURRENCY', 8), patch.dict(adapter.os.environ):
            adapter.configure({'timeout_minutes':10}, self.root)
            self.assertEqual(adapter.FILE_CONCURRENCY, 1)

    def test_mcr_rejects_unsupported_parallelism(self):
        with self.assertRaisesRegex(ValueError, '单轮 LCR'):
            self.app.submit(dict(action='create', name='mcr', suite='mcr',
                                 dataset='mcr-pilot1.jsonl', case_jobs=4))

    def test_frozen_concurrency_cannot_be_overridden(self):
        argv = ['cloud.py', 'review', '--run-id', 'test', '--case-jobs', '2']
        with patch('sys.argv', argv), self.assertRaises(SystemExit) as caught:
            cloud.main()
        self.assertEqual(caught.exception.code, 2)
