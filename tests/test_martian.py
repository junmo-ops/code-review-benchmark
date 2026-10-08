import asyncio,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from evaluators import martian,registry
from suites.registry import get_suite

class MartianTests(unittest.TestCase):
    def test_original_default_for_historical_runs_and_drift(self):
        self.assertEqual(registry.verify({}),'original')
        cfg={'evaluator':registry.freeze('martian',get_suite('aacr'))}
        self.assertEqual(registry.verify(cfg),'martian')
        cfg['evaluator']['parameters']['temperature']=1
        with self.assertRaises(ValueError):registry.verify(cfg)
    def test_invalid_json_values_fail(self):
        good={'match':True,'confidence':.9,'reasoning':'same issue'}
        self.assertEqual(martian.validate_result(good),good)
        for patch in ({'match':'false'},{'confidence':float('nan')},{'confidence':2},{'reasoning':''}):
            with self.assertRaises(ValueError):martian.validate_result(dict(good,**patch))
    def test_native_metrics_preserved_even_multiple_matches(self):
        class Judge:
            async def match_comment(self,g,c):return {'match':True,'confidence':.9,'reasoning':'same'}
        result=asyncio.run(martian.upstream.evaluate_review(Judge(),[{'comment':'first'},{'comment':'second'}],['one finding']))
        self.assertEqual(result['tp'],2)
        self.assertEqual(result['precision'],2)
    def test_transport_captures_actual_prompt_and_rejects_truncation(self):
        from types import SimpleNamespace
        class Response:
            choices=[SimpleNamespace(finish_reason='length')]
            def model_dump(self):return {'choices':[{'finish_reason':'length'}]}
        class Client:
            async def create(self,**kwargs):return Response()
        client=SimpleNamespace(chat=SimpleNamespace(completions=Client()))
        with tempfile.TemporaryDirectory() as d:
            judge=martian.Judge(client,'fake',Path(d),registry.PARAMETERS)
            with self.assertRaises(ValueError):asyncio.run(judge.match_comment('reference','candidate'))
            request=json.loads((Path(d)/'judge/000001.request.json').read_text())
            self.assertEqual(request['messages'][1]['content'],martian.upstream.JUDGE_PROMPT.format(golden_comment='reference',candidate='candidate'))
            self.assertTrue((Path(d)/'judge/000001.failure.json').exists())
    def test_data_maps_actual_reference_text_not_raw_record(self):
        row={'reference_comments':[{'text':'a','path':'x','start_line':3}]}
        self.assertEqual(martian.golden_rows(row,'aacr')[0]['comment'],'a')
        row={'annotation':{'substantive_comment_ids':['b'],'comments':[{'comment_id':'a','body':'reply'},{'comment_id':'b','body':'issue'}]}}
        self.assertEqual(martian.golden_rows(row,'swe_prbench_llm')[0]['comment'],'issue')

if __name__=='__main__':unittest.main()
