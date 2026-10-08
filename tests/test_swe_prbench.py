import copy,json,tempfile,unittest
from pathlib import Path
from suites.swe_prbench.adapter import SWEPRBenchSuite,records
from suites.swe_prbench.evaluator import make_input,make_output,validate_judge,changed_lines
from eval_harness.schema import JudgeOutput,JudgeClassification,HumanCommentStatus
from eval_harness.scorer import compute_dimension_scores

class SWETests(unittest.TestCase):
    def setUp(self):
        self.row={'task_id':'sample__1','repo':'owner/repo','pr_number':1,'base_commit':'a'*40,'head_commit':'b'*40,
            'diff_patch':'diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n x\n-old\n+new\n',
            'annotation':{'comments':[{'comment_id':'h1','body':'missing null check','file':'x.py','line':2,'is_initiating_comment':True},{'comment_id':'reply','body':'ack','is_initiating_comment':False}]},
            'contexts':{'A':{'rendered':'frozen context','pipeline_version':'v0.4.1'}}}
        self.answer={'review':{'comments':[{'content':'missing null check','path':'x.py','start_line':2}]}}
        self.inp=make_input(self.row);self.out=make_output(self.row,self.answer,'test')
        self.j={'agent_classifications':[{'comment_id':'a_1','classification':'CONFIRMED','matched_human_comment_id':'h1','actionability_score':5}],
            'human_comment_statuses':[{'comment_id':'h1','status':'CAUGHT','matched_agent_comment_id':'a_1'}]}
    def test_contract_does_not_expose_reference(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'data.jsonl';p.write_text(json.dumps(self.row)+'\n')
            case=SWEPRBenchSuite().load_cases(p)[0]
            self.assertEqual(set(vars(case)),{'instance_id','repo','base_commit','head_commit'})
            self.assertEqual(case.instance_id,'sample__1')
    def test_annotation_selection_and_line_mapping(self):
        self.assertEqual(len(self.inp.human_comments),1)
        self.assertEqual(changed_lines(self.row['diff_patch']),{'x.py':{2}})
        self.assertFalse(self.out.comments[0].is_outside_diff)
        self.row['annotation']['substantive_comment_ids']=['reply']
        self.assertEqual(make_input(self.row).human_comments[0]['comment_id'],'reply')
    def test_rejects_missing_duplicate_and_inconsistent_judgement(self):
        validate_judge(self.j,self.inp,self.out)
        for data in [dict(self.j,agent_classifications=[]),dict(self.j,agent_classifications=self.j['agent_classifications']*2),dict(self.j,human_comment_statuses=[{'comment_id':'h1','status':'MISSED'}])]:
            with self.assertRaises(ValueError):validate_judge(data,self.inp,self.out)
    def test_upstream_metric_perfect_and_empty_answer(self):
        judge=JudgeOutput('sample__1','lcr_repository_variant',[JudgeClassification('a_1','CONFIRMED','h1',5,None)],[HumanCommentStatus('h1','CAUGHT','a_1')],'test','v1.0')
        score=compute_dimension_scores(self.inp,self.out,judge)
        self.assertEqual((score['precision'],score['recall'],score['f1_score']),(1,1,1))
        self.out.comments=[];judge.agent_classifications=[];judge.human_comment_statuses=[HumanCommentStatus('h1','MISSED',None)]
        score=compute_dimension_scores(self.inp,self.out,judge)
        self.assertEqual(score['recall'],0)
    def test_invalid_source_not_accepted(self):
        del self.row['annotation']
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad.jsonl';p.write_text(json.dumps(self.row))
            with self.assertRaises(ValueError):records(p)
if __name__=='__main__':unittest.main()
