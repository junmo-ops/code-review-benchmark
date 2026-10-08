import asyncio, hashlib, json, os, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from runner.storage import ROOT
from suites.codefuse_cr.adapter import CodeFuseSuite, records, normalize_patch
from scoring import registry, martian
from evaluators import registry as legacy

class CodeFuseTests(unittest.TestCase):
    def setUp(self):
        self.suite=CodeFuseSuite()
        self.row=json.loads((ROOT/'data/codefuse-pilot1.jsonl').read_text())

    def test_review_range_never_uses_merged_commit(self):
        case=self.suite.load_cases(ROOT/'data/codefuse-pilot1.jsonl')[0]
        self.assertEqual(case.head_commit,self.row['commit_to_review']['head_commit'])
        self.assertNotEqual(case.head_commit,self.row['merged_commit'])
        self.assertFalse(hasattr(case,'reference_review_comments'))
        self.assertEqual(self.suite.reference_comments(self.row)[0]['comment'],self.row['reference_review_comments'][0]['text'])

    def test_original_rejected_and_legacy_identity_preserved(self):
        with self.assertRaisesRegex(ValueError,'官方原始评分'):registry.freeze('original',self.suite)
        old={'evaluator':legacy.freeze('martian',SimpleNamespace(id='aacr'))}
        self.assertEqual(registry.verify(old),'martian')
        new={'suite':{'id':'codefuse_cr'},'evaluator':registry.freeze('martian',self.suite)}
        self.assertEqual(registry.verify(new),'martian')
        new['evaluator']['parameters']['max_tokens']=1
        with self.assertRaises(ValueError):registry.verify(new)

    def test_bad_records_fail_and_unicode_separator_is_not_new_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'data.jsonl'
            row=dict(self.row,body='text\u2028next')
            p.write_text(json.dumps(row,ensure_ascii=False)+'\n')
            self.assertEqual(len(records(p)),1)
            row['reference_review_comments']=[];p.write_text(json.dumps(row))
            with self.assertRaisesRegex(ValueError,'参考意见'):records(p)
            p.write_text(json.dumps(self.row)+'\n'+json.dumps(self.row))
            with self.assertRaisesRegex(ValueError,'重复'):records(p)

    def test_patch_header_normalization_does_not_hide_code_changes(self):
        patch_text=self.row['commit_to_review']['patch_to_review']
        changed=patch_text.replace('def run_tests(self):','packages = []')
        self.assertEqual(normalize_patch(patch_text),normalize_patch(changed))
        self.assertNotEqual(normalize_patch(patch_text),normalize_patch(patch_text.replace("+    'idna>=2.5,<4'","+    'idna>=2.5,<5'")))
        case=self.suite.load_cases(ROOT/'data/codefuse-pilot1.jsonl')[0]
        with patch('suites.codefuse_cr.adapter.subprocess.run',return_value=SimpleNamespace(stdout='wrong patch')):
            with self.assertRaisesRegex(ValueError,'不一致'):
                self.suite.check_repository(case,Path('/tmp/repo'),ROOT/'data/codefuse-pilot1.jsonl')

    def test_partial_judge_error_never_publishes_complete_score(self):
        case=self.suite.load_cases(ROOT/'data/codefuse-pilot1.jsonl')[0]
        class Client:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
        async def fail(*args):return {'errors_count':1,'errors':['failed']}
        cfg={'evaluator':{'parameters':dict(legacy.PARAMETERS)},'judge_model':'test',
             'judge_endpoint_sha256':hashlib.sha256(b'https://example.invalid').hexdigest()}
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp);(run/'dataset.jsonl').write_text(json.dumps(self.row)+'\n')
            (run/'results').mkdir()
            (run/'results'/f'{case.safe_id}.json').write_text(json.dumps({'instance_id':case.instance_id,'base_commit':case.base_commit,'head_commit':case.head_commit,'review':{'status':'complete','comments':[{'content':'finding'}]}}))
            with patch.dict(os.environ,{'JUDGE_API_KEY':'fake','JUDGE_BASE_URL':'https://example.invalid','JUDGE_MODEL':'test','JUDGE_USE_MOCK':'false'}),patch('scoring.martian.resolve_run_suite',return_value=self.suite),patch('scoring.martian.verify'),patch('openai.AsyncOpenAI',return_value=Client()),patch.object(martian.upstream,'evaluate_review',side_effect=fail):
                with self.assertRaisesRegex(ValueError,'部分失败'):asyncio.run(martian.evaluate(run,cfg))
            self.assertFalse((run/'metrics/martian.json').exists())
            self.assertEqual(json.loads((run/'metrics/martian.failure.json').read_text())['status'],'failed')

if __name__=='__main__':unittest.main()
