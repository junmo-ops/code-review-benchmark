import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from tests import test_swe_prbench as fixtures
from suites.swe_prbench_llm.evaluator import score_judgement,evaluate,report
from suites.swe_prbench_llm.adapter import SWELLMSuite
from suites.registry import resolve_run_suite
from runner.storage import ROOT
from eval_harness.schema import AgentComment,JudgeOutput,JudgeClassification,HumanCommentStatus

class LLMTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.SWETests();f.setUp();self.row,self.inp,self.out,self.j=f.row,f.inp,f.out,f.j
    def test_no_similarity_backup(self):
        self.j['agent_classifications'][0].update(classification='PLAUSIBLE',matched_human_comment_id=None)
        self.j['human_comment_statuses'][0].update(status='MISSED',matched_agent_comment_id=None)
        # Text and location are identical; only the LLM verdict can produce a hit.
        with patch('eval_harness.matching._get_embedding_model',side_effect=AssertionError('embedding forbidden')):
            s=score_judgement(self.inp,self.out,self.j)
        self.assertEqual(s['matched_count'],0)
    def test_duplicate_confirmation_counts_once(self):
        self.out.comments.append(AgentComment('a_2','same issue','x.py',2,None,False))
        self.j['agent_classifications'].append(dict(self.j['agent_classifications'][0],comment_id='a_2'))
        s=score_judgement(self.inp,self.out,self.j)
        self.assertEqual((s['precision'],s['recall'],s['matched_count']),(.5,1,1))
        self.assertEqual(s['duplicate_confirmed_ids'],['a_2'])
    def test_invalid_judge_fails_not_zero(self):
        self.j['human_comment_statuses']=[]
        with self.assertRaises(ValueError):score_judgement(self.inp,self.out,self.j)
    def test_old_runs_still_resolve(self):
        for run in ['aacr-smoke-20260930-230330','swe-prbench-smoke-20260930-231600']:
            path=ROOT/'runs'/run/'run-config.json'
            if path.exists():resolve_run_suite(json.loads(path.read_text()))
    def test_eval_report_and_no_overwrite_without_network(self):
        with tempfile.TemporaryDirectory() as d:
            run=Path(d);(run/'dataset.jsonl').write_text(json.dumps(self.row)+'\n')
            (run/'results').mkdir()
            answer={'instance_id':'sample__1','repo':'owner/repo','base_commit':'a'*40,'head_commit':'b'*40,'review':{'status':'complete','comments':[{'path':'x.py','start_line':2,'content':'missing null check'}]}}
            (run/'results/sample__1.json').write_text(json.dumps(answer))
            cfg={'review_model':'test','judge_model':'test','judge_endpoint_sha256':'test','suite':{'parameters':SWELLMSuite.parameters,'identity':{}}}
            verdict=JudgeOutput('sample__1','internal_llm_only_v1',[JudgeClassification('a_1','CONFIRMED','h1',5,'same')],[HumanCommentStatus('h1','CAUGHT','a_1')],'test','v1.0')
            with patch('socket.socket',side_effect=AssertionError('network forbidden')),patch('suites.swe_prbench_llm.evaluator.JudgeRouter'),patch('suites.swe_prbench_llm.evaluator.run_judge',return_value=verdict),patch('eval_harness.matching.normalize_judge_alignment',side_effect=AssertionError('matching forbidden')):
                evaluate(run,cfg)
                report(run,cfg)
                with self.assertRaises(ValueError):evaluate(run,cfg)
            self.assertEqual(json.loads((run/'metrics/swe_prbench_llm.json').read_text())['summary']['f1'],1)
            self.assertIn('不使用嵌入模型',(run/'report.zh-CN.md').read_text())
    def test_router_uses_frozen_output_budget(self):
        from suites.swe_prbench_llm.evaluator import JudgeRouter,BaseJudgeRouter
        router=JudgeRouter.__new__(JudgeRouter);router.output_limit=8192
        with patch.object(BaseJudgeRouter,'generate',return_value='ok') as generate:
            self.assertEqual(router.generate('m','s','u',4000),'ok')
            self.assertEqual(generate.call_args.args,('m','s','u',8192))

    def test_reuse_rejects_mismatched_model_and_preserves_source(self):
        import hashlib
        from tools.reuse_answers import reuse
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'source';target=root/'target'
            raw=json.dumps(self.row)+'\n';sha=hashlib.sha256(raw.encode()).hexdigest()
            cfg={'suite':{'id':'swe_prbench_llm','identity':SWELLMSuite().identity()},'dataset_sha256':sha,
                 'reviewer_id':'lcr','tool_hashes':{},'review_model':'same','review_endpoint_sha256':'same'}
            for folder in (source,target):
                folder.mkdir();(folder/'dataset.jsonl').write_text(raw);(folder/'run-config.json').write_text(json.dumps(cfg))
            (source/'results').mkdir()
            answer={'instance_id':'sample__1','repo':'owner/repo','base_commit':'a'*40,'head_commit':'b'*40,'review':{'status':'complete','comments':[]}}
            path=source/'results/sample__1.json';path.write_text(json.dumps(answer));before=path.read_bytes()
            (target/'run-config.json').write_text(json.dumps(dict(cfg,review_model='different')))
            with self.assertRaises(ValueError):reuse(source,target)
            self.assertFalse((target/'results').exists())
            (target/'run-config.json').write_text(json.dumps(cfg));reuse(source,target)
            self.assertEqual(path.read_bytes(),before)
            self.assertTrue((target/'answer-reuse.json').exists())
            with self.assertRaises(ValueError):reuse(source,target)

if __name__=='__main__':unittest.main()
