"""Suite isolation, frozen provenance and offline end-to-end regression checks."""
import argparse,asyncio,hashlib,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from runner import cloud
from runner.contracts import ReviewCase
from runner.process import capture_process
from suites import registry
from reviewers.lcr import adapter as lcr

class SuiteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def freeze(self, suite='aacr', dataset=None):
        run=self.root/'run';run.mkdir(exist_ok=True)
        cloud.init_run(argparse.Namespace(suite=suite,reviewer='lcr',dataset=str(dataset or ROOT/'data/pilot5.jsonl'),model='test',repos_root=None,timeout_minutes=10),run)
        return run,json.loads((run/'run-config.json').read_text())
    def test_references_never_enter_review_case(self):
        cases=registry.get_suite('aacr').load_cases(ROOT/'data/pilot5.jsonl')
        self.assertEqual(len(cases),5)
        self.assertEqual(set(vars(cases[0])),{'instance_id','repo','base_commit','head_commit'})
    def test_unknown_suite_and_missing_capability_fail_before_freeze(self):
        with self.assertRaisesRegex(ValueError,'尚未接入'):self.freeze('not_installed_suite')
        self.assertFalse((self.root/'run/run-config.json').exists())
        suite=Mock(required_capabilities=frozenset({'execute_tests'}))
        with self.assertRaisesRegex(ValueError,'execute_tests'):registry.ensure_compatible(suite,lcr)
    def test_freeze_and_version_drift(self):
        run,cfg=self.freeze()
        self.assertEqual(cfg['suite']['provenance']['status'],'retained_pilot_source')
        self.assertEqual(registry.resolve_run_suite(cfg).id,'aacr')
        cfg['suite']['identity']['adapter_version']='changed'
        with self.assertRaisesRegex(ValueError,'已变化'):registry.resolve_run_suite(cfg)
        self.assertEqual(registry.resolve_run_suite(cfg,verify=False).id,'aacr')
    def test_windows_local_runtime_is_not_mislabeled_as_mac(self):
        runtime=self.root/'local-runtime.json';runtime.write_text('{"platform":"Windows"}')
        with patch.object(cloud,'ROOT',self.root),patch.object(cloud.platform,'system',return_value='Windows'),patch.object(cloud.adapter,'tool_identity',return_value={}):
            run,cfg=self.freeze()
        self.assertEqual(cfg['runtime_mode'],'windows_local')
    def test_old_batches_remain_readable_without_rewriting(self):
        self.assertEqual(registry.resolve_run_suite({'schema_version':1}).id,'aacr')
        with self.assertRaisesRegex(ValueError,'缺少'):registry.resolve_run_suite({'schema_version':2})
    def test_second_suite_routes_own_scorer_without_aacr(self):
        # Synthetic adapter demonstrates dispatch only; not a claimed SWE-PRBench adapter.
        class Other:
            id='test_other';name='Synthetic test';version='1';parameters={}
            required_capabilities=frozenset({'git_range','findings'})
            def load_cases(self,path):return [ReviewCase('case','test/repo','a'*40,'b'*40)]
            def identity(self):return {'id':self.id,'version':self.version}
            def provenance(self,path):return {'source':'synthetic'}
            def evaluate(self,cases,run,cfg):(run/'own-score.json').write_text('{"metric":"own"}')
            def report(self,run,cfg):raise AssertionError('not used')
        suite=Other();dataset=self.root/'other.json';dataset.write_text('{}')
        with patch.dict(registry.SUITES,{'test_other':suite}):
            run,cfg=self.freeze('test_other',dataset)
            argv=['cloud.py','eval','--run-id','run','--runs-root',str(self.root)]
            with patch.object(sys,'argv',argv),patch.object(registry.get_suite('aacr'),'evaluate',side_effect=AssertionError('wrong scorer')):
                cloud.main()
            self.assertTrue((run/'own-score.json').exists())
            self.assertFalse((run/'metrics').exists())
    def test_failed_partial_answer_is_rejected(self):
        item=registry.get_suite('aacr').load_cases(ROOT/'data/pilot1.jsonl')[0]
        for raw in [{'status':'partial','comments':[]},{'status':'success','comments':[],'warnings':[{'type':'subtask_error'}]}]:
            with self.assertRaises(ValueError):lcr.convert(item,raw)
    def test_process_timeout_persists_output_and_status(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            capture_process([sys.executable,'-u','-c','import time; print("started"); time.sleep(20)'],self.root,'process',timeout=.3,cwd=self.root)
        record=json.loads((self.root/'process.execution.json').read_text())
        self.assertEqual(record['status'],'timed_out')
        self.assertIn('started',(self.root/'process.stdout.json').read_text())
    def test_five_case_replay_and_three_round_scoring_no_network(self):
        run,cfg=self.freeze()
        cases=registry.get_suite('aacr').load_cases(run/'dataset.jsonl')
        rows=[json.loads(line) for line in (run/'dataset.jsonl').read_text().splitlines()]
        # Replay deterministic CLI-shaped answers; every annotation has an exact answer.
        for case,row in zip(cases,rows):
            result={'status':'success','comments':[{'filePath':c['path'],'startLine':c['start_line'],'endLine':c['end_line'],'description':c['text']} for c in row['reference_comments']]}
            result_path=run/'results'/(case.safe_id+'.json');result_path.parent.mkdir(exist_ok=True)
            result_path.write_text(json.dumps(lcr.convert(case,result)))
        env=dict(os.environ,JUDGE_API_KEY='offline-test-key',JUDGE_BASE_URL='https://judge.invalid/v1',JUDGE_MODEL='offline-test',JUDGE_USE_MOCK='false')
        cfg['judge_model']=None;cfg['judge_endpoint_sha256']=None
        (run/'run-config.json').write_text(json.dumps(cfg))
        worker=r'''
import asyncio,runpy,sys,socket
from types import SimpleNamespace
from unittest.mock import patch
from suites.aacr import evaluator,report
class Client:
    def __init__(self,**kwargs):self.chat=SimpleNamespace(completions=self)
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def create(self,**kwargs):return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='yes'))],usage=None)
def offline_report(run,cfg):asyncio.run(report.generate_report(run,offline=True))
sys.argv=['suites.aacr','eval','--run',sys.argv[1]]
with patch('openai.AsyncOpenAI',Client),patch.object(evaluator,'stage_report',offline_report),patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
    runpy.run_module('suites.aacr',run_name='__main__')
'''
        completed=subprocess.run([sys.executable,'-c',worker,str(run)],cwd=ROOT,env=env,capture_output=True,text=True)
        self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)
        metrics=json.loads((run/'metrics/metrics_ocr_average.json').read_text())
        self.assertEqual(metrics['eval_rounds'],3)
        self.assertEqual(metrics['summary']['evaluated_instances'],5)
        self.assertEqual(metrics['summary']['semantic_f1'],1)
        self.assertIn('AACR-Bench',(run/'report.zh-CN.md').read_text())
        self.assertTrue((run/'evaluation-config.json').exists())
        self.assertEqual([row['status'] for row in cloud.states(cases,run)],['complete']*5)
        # Retry must skip complete answers without executing LCR.
        cloud.configure(cfg,run)
        with patch.object(lcr,'run_lcr',side_effect=AssertionError('must not rerun')):
            lcr.review_case(cases[0],run,False,__import__('threading').Event())

if __name__=='__main__':unittest.main()
