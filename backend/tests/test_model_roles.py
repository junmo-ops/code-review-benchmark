import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from webui.queue import DurableDashboard
from webui.profiles import create_profile, compose_roles
from regression_fixtures import seed_datasets

class ModelRoleTests(unittest.TestCase):
    def test_independent_models_are_frozen_in_durable_run(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'BENCH_CONFIG_ROOT':tmp,'BENCH_DATA_ROOT':tmp}):
            root=Path(tmp);dashboard=DurableDashboard(root)
            seed_datasets(root)
            ids=[]
            for provider in ('qwen','deepseek'):
                ids.append(create_profile(dashboard,dict(provider=provider,name=provider+' 测试',model=provider,base_url='https://'+provider+'.test/v1',api_key=provider+'-secret'))['id'])
            self.assertTrue(all(p['review_ready'] for p in dashboard.profiles() if p['id'] in ids))
            self.assertNotIn('JUDGE_MODEL',json.loads((root/ids[0]).read_text()))
            with self.assertRaises(ValueError):
                compose_roles(dashboard,ids[0],None)
            result=dashboard.submit(dict(action='create',suite='aacr',dataset='pilot1.jsonl',name='paired',review_profile=ids[0],judge_profile=ids[1]))
            job=dashboard.queue.jobs()[result['job_id']]
            selected=job['profile'];frozen=json.loads((root/selected).read_text())
            self.assertEqual(frozen['LCR_LLM_MODEL'],'qwen');self.assertEqual(frozen['JUDGE_MODEL'],'deepseek')
            self.assertEqual(frozen['JUDGE_API_KEY'],'deepseek-secret')
            self.assertEqual((root/selected).stat().st_mode&0o777,0o600)
            original=json.loads((root/ids[0]).read_text());original['LCR_LLM_MODEL']='changed';(root/ids[0]).write_text(json.dumps(original))
            restarted=DurableDashboard(root)
            claimed,request,spec=restarted.queue.claim()
            self.assertEqual(request['profile'],selected)
            self.assertEqual(restarted.environment(selected)['LCR_LLM_MODEL'],'qwen')
            self.assertTrue(next(p for p in restarted.profiles() if p['id']==selected)['internal'])
            self.assertNotIn('qwen-secret',json.dumps(job))
            self.assertNotIn('deepseek-secret',json.dumps(job))
    def test_same_model_legacy_judge_and_no_judge_track(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'BENCH_CONFIG_ROOT':tmp,'BENCH_DATA_ROOT':tmp}):
            root=Path(tmp);dashboard=DurableDashboard(root)
            old=dict(LCR_LLM_MODEL='deepseek',LCR_LLM_BASE_URL='https://model.test/v1',LCR_LLM_TOKEN='review-secret',JUDGE_MODEL='legacy-judge',JUDGE_BASE_URL='https://judge.test/v1',JUDGE_API_KEY='judge-secret')
            (root/'model.local.json').write_text(json.dumps(old))
            same=compose_roles(dashboard,'model.local.json','model.local.json');self.assertEqual(same['JUDGE_MODEL'],'deepseek')
            legacy=compose_roles(dashboard,'model.local.json','legacy-judge:model.local.json');self.assertEqual(legacy['JUDGE_MODEL'],'legacy-judge')
            no_judge=compose_roles(dashboard,'model.local.json',None,False);self.assertEqual(no_judge['JUDGE_MODEL'],'')
            self.assertEqual(json.loads((root/'model.local.json').read_text()),old)
