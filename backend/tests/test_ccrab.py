import copy
import json
from pathlib import Path
import tempfile
import unittest
from executable.ccrab import load, review_input, findings
from runner.contracts import ReviewCase
from runner.storage import validate_answer


class CCRABTests(unittest.TestCase):
    def setUp(self):
        self.row, self.provenance = load()
        self.case = ReviewCase(self.row['instance_id'], self.row['repo'], self.row['base_commit'], self.row['commit_to_review']['head_commit'])
        self.answer = dict(instance_id=self.case.instance_id, repo=self.case.repo, base_commit=self.case.base_commit,
                           head_commit=self.case.head_commit, review={'status':'complete','comments':[]})

    def test_pinned_single_case_and_reference_isolation(self):
        self.assertEqual(self.provenance['source_instances'],184)
        self.assertEqual(set(review_input(self.row)),{'instance_id','repo','base_commit','head_commit','patch_to_review'})

    def test_findings_mapping_and_empty_answer(self):
        self.assertEqual(findings(self.answer,self.row)['tools']['lcr']['findings'],[])
        self.answer['review']['comments']=[dict(path='a.py',start_line=1,end_line=2,content='example finding')]
        result=findings(self.answer,self.row)['tools']['lcr']['findings'][0]
        self.assertEqual(result['issue_content'],'example finding')
        self.assertNotIn('reference_review_comments',result)

    def test_wrong_case_and_partial_answer_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'answer.json'
            for key,value in [('head_commit','0'*40),('instance_id','other')]:
                altered=copy.deepcopy(self.answer);altered[key]=value;p.write_text(json.dumps(altered))
                with self.assertRaises(ValueError):validate_answer(self.case,p)
            self.answer['review']['status']='partial';p.write_text(json.dumps(self.answer))
            with self.assertRaises(ValueError):validate_answer(self.case,p)

    def test_invalid_finding_rejected(self):
        for path in ('../a.py','/a.py','C:\\a.py'):
            self.answer['review']['comments']=[dict(path=path,start_line=1,end_line=1,content='test')]
            with self.assertRaises(ValueError):findings(self.answer,self.row)

if __name__=='__main__':unittest.main()
