import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from webui.service import Dashboard,save
from regression_fixtures import seed_datasets

class UXWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.app=Dashboard(self.root)
        seed_datasets(self.root)
        save(self.root/'model.local.json',dict(LCR_LLM_MODEL='test',LCR_LLM_TOKEN='test',JUDGE_MODEL='test',JUDGE_API_KEY='test'))
    def tearDown(self):self.app.shutdown();self.temp.cleanup()
    def run_create(self,fail=False,start=True):
        steps=[[('init',None,{})],[('check',None,None),('review','case',None),('eval',None,None)]]
        cmd=[sys.executable,'-c','raise SystemExit('+('1' if fail else '0')+')']
        with patch.object(self.app,'plan',side_effect=steps) as plan,patch.object(self.app,'command',return_value=(cmd,dict(os.environ),10)) as command:
            job=self.app.submit(dict(action='create',suite='aacr',dataset='pilot1.jsonl',name='ux',start_after_create=start))
            self.app.worker.join(10)
            return self.app.jobs[job['job_id']],plan.call_count,command.call_count
    def test_create_and_start_runs_one_chained_job(self):
        job,plans,calls=self.run_create();self.assertEqual(calls,4);self.assertEqual(plans,2)
        self.assertEqual(job['status'],'succeeded');self.assertEqual(job['total_steps'],4)
    def test_failed_creation_never_starts_model_steps(self):
        job,plans,calls=self.run_create(fail=True);self.assertEqual(calls,1);self.assertEqual(plans,1);self.assertEqual(job['status'],'failed')
    def test_save_only_never_runs_workflow(self):
        job,plans,calls=self.run_create(start=False);self.assertEqual(calls,1);self.assertEqual(plans,1)
    def test_non_boolean_start_rejected(self):
        with self.assertRaises(ValueError):self.app.submit(dict(action='create',start_after_create='false'))
