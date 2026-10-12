import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from comparison.scorer import count_matches, counts, PROTOCOL, PARAMETERS, identity
from comparison.report import build
from comparison.service import create
from comparison.runner import run
from webui.queue import DurableDashboard
from runner.storage import save

ROOT=Path(__file__).resolve().parents[1]

class MatchingTests(unittest.TestCase):
    def test_one_candidate_cannot_count_twice(self):
        result=count_matches(2,1,[(0,0),(1,0)])
        self.assertEqual((result['precision'],result['recall'],result['tp']),(1,.5,1))
    def test_augmenting_path_not_greedy(self):
        result=count_matches(2,2,[(0,0),(0,1),(1,0)])
        self.assertEqual(result['pairs'],[(0,1),(1,0)])
        self.assertEqual(result['f1'],1)
    def test_empty_and_duplicate_edges(self):
        self.assertEqual(count_matches(2,0,[])['fn'],2)
        self.assertEqual(count_matches(1,1,[(0,0),(0,0)])['tp'],1)
        with self.assertRaises(ValueError):count_matches(1,1,[(0,2)])

class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'BENCH_CONFIG_ROOT':str(self.root/'configs'),'BENCH_DATA_ROOT':str(self.root)})
        self.env.start();self.app=DurableDashboard(ROOT)
        for i in (1,2):
            save(self.app.configs/f'model.{i}.local.json',dict(LCR_LLM_MODEL=f'model-{i}',LCR_LLM_TOKEN='secret',LCR_LLM_BASE_URL='https://invalid.example/v1'))
        self.data=dict(name='test',suite='aacr',dataset='pilot1.jsonl',models=['model.1.local.json','model.2.local.json'],judge_profile='model.1.local.json',timeout_minutes=1)
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    def test_creation_queue_freezes_and_has_no_secrets(self):
        result=create(self.app,self.data);folder=self.app.run_dir(result['key']);manifest=json.loads((folder/'experiment.json').read_text())
        self.assertEqual(len(manifest['groups']),2)
        self.assertNotIn('secret',json.dumps(manifest));self.assertNotIn('secret',json.dumps(self.app.queue.jobs()))
        self.assertEqual(manifest['protocol'],PROTOCOL)
        claimed,request,spec=self.app.queue.claim()
        self.assertEqual(self.app.plan(claimed,request,spec),[('comparison',None,None)])
        self.assertEqual(self.app.snapshot()['comparisons'][0]['status'],'running')
        with self.assertRaises(ValueError):create(self.app,self.data)
        self.assertEqual(build(folder)['common_cases'],[])
    def test_reject_same_endpoint_model(self):
        save(self.app.configs/'model.2.local.json',dict(LCR_LLM_MODEL='model-1',LCR_LLM_TOKEN='other',LCR_LLM_BASE_URL='https://invalid.example/v1'))
        with self.assertRaises(ValueError):create(self.app,self.data)
    def test_common_denominator_excludes_failure_without_zero_score(self):
        result=create(self.app,self.data);folder=self.app.run_dir(result['key']);m=json.loads((folder/'experiment.json').read_text());case=m['case_ids'][0];safe=case.replace('/','__')
        for idx,g in enumerate(m['groups']):
            root=folder.parent/g['key'];save(root/'comparison-status'/f'{safe}.json',{'status':'complete' if idx==0 else 'review_failed','review_seconds':10,'error':'TimeoutError' if idx else None})
            if idx==0:
                save(root/'results'/f'{safe}.json',{'review':{'status':'complete','summary':{'input_tokens':5,'output_tokens':5,'total_tokens':10}}})
                save(root/'comparison-scores'/f'{safe}.json',{'status':'complete','metrics':counts(1,1,2)})
        r=build(folder);self.assertEqual(r['common_cases'],[])
        self.assertIsNone(r['groups'][0]['common_quality']);self.assertEqual(r['groups'][1]['failed'],1)
        self.assertEqual(r['groups'][0]['own_quality']['recall'],.5)
    def test_serial_orchestration_continues_after_failed_group(self):
        result=create(self.app,self.data);folder=self.app.run_dir(result['key']);m=json.loads((folder/'experiment.json').read_text());visited=[]
        def command(key,stage,profile,**kw):return [key,stage],{},60
        def execute(cmd,env,limit,path):
            key,stage=cmd;visited.append((key,stage));root=folder.parent/key
            if stage=='init':root.mkdir(exist_ok=True)
            return int(stage=='review' and key.endswith('g1'))
        async def score(root,suite,case,env):
            save(root/'comparison-scores'/f'{case}.json',{'status':'complete','metrics':counts(1,1,2)})
        with patch.object(self.app,'command',side_effect=command),patch('comparison.runner.execute',side_effect=execute),patch('comparison.runner.score_case',side_effect=score):
            self.assertEqual(run(self.app,folder),1)
        self.assertEqual(visited[-1],(m['groups'][1]['key'],'review'))
        with self.assertRaises(ValueError):run(self.app,folder)
    def test_config_drift_stops_before_execution(self):
        result=create(self.app,self.data);folder=self.app.run_dir(result['key']);m=json.loads((folder/'experiment.json').read_text())
        p=self.app.configs/m['groups'][1]['profile'];p.write_text('{}')
        with patch('comparison.runner.execute') as execute:
            with self.assertRaises(ValueError):run(self.app,folder)
            execute.assert_not_called()

class ScoringIntegrationTests(unittest.TestCase):
    def test_score_uses_valid_answer_and_persists_decisions_without_real_api(self):
        from comparison.scorer import score_case
        from unittest.mock import AsyncMock, MagicMock
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            (folder/'dataset.jsonl').write_bytes((ROOT/'data/pilot1.jsonl').read_bytes())
            row=json.loads((folder/'dataset.jsonl').read_text().strip());case=row['instance_id'];filename=case.replace('/','__')+'.json'
            data=dict(instance_id=case,base_commit=row['base_commit'],head_commit=row['head_commit'],review={'status':'complete','comments':[{'content':'test A'},{'content':'test B'}]})
            save(folder/'results'/filename,data)
            decision=dict(match=True,confidence=1.,reasoning='Test fixture only')
            with patch('openai.AsyncOpenAI') as client,patch('comparison.scorer.Judge') as judge:
                client.return_value.__aenter__=AsyncMock(return_value=MagicMock())
                client.return_value.__aexit__=AsyncMock(return_value=False)
                judge.return_value.match_comment=AsyncMock(return_value=decision)
                value=asyncio.run(score_case(folder,'aacr',case,dict(JUDGE_API_KEY='fake',JUDGE_BASE_URL='https://invalid.example',JUDGE_MODEL='test')))
                self.assertEqual(value['metrics']['tp'],2)
                self.assertEqual(value['metrics']['precision'],1)
                self.assertEqual(len(value['decisions']),4)
                self.assertTrue((folder/'comparison-scores'/filename).exists())
                with self.assertRaises(ValueError):asyncio.run(score_case(folder,'aacr',case,{}))
