import json,unittest
from types import SimpleNamespace
from reviewers.cline_skill import adapter as a

class ClineAnswerTests(unittest.TestCase):
    def setUp(self):
        a.CFG={'review_model':'test'}
        self.item=SimpleNamespace(instance_id='case',repo='test/repo',base_commit='a'*40,head_commit='b'*40)
        self.rows=[dict(event=dict(type='content_end',contentType='tool',toolName='skills',output='<command-name>benchmark-review</command-name>')),dict(type='run_result',finishReason='completed',text=json.dumps(dict(status='complete',comments=[])),aggregateUsage=dict(inputTokens=100,outputTokens=10,cacheReadTokens=90,totalCost=0))]
    def test_empty_complete_is_valid_but_not_free(self):
        out=a.convert(self.item,self.rows)
        self.assertEqual(out['review']['comments'],[])
        self.assertEqual(out['review']['summary']['total_tokens'],110)
        self.assertEqual(out['runtime']['cost'],'not_verified')
    def test_missing_skill_or_incomplete_run_rejected(self):
        with self.assertRaises(ValueError):a.convert(self.item,self.rows[1:])
        self.rows[1]['finishReason']='timeout'
        with self.assertRaises(ValueError):a.convert(self.item,self.rows)
    def test_bad_json_and_invalid_findings_rejected(self):
        for text in ['not JSON',json.dumps(dict(status='failed',comments=[])),json.dumps(dict(status='complete',comments=[dict(path='../secret',start_line=1,end_line=1,content='x')]))]:
            self.rows[1]['text']=text
            with self.assertRaises(ValueError):a.convert(self.item,self.rows)

if __name__=='__main__':unittest.main()
