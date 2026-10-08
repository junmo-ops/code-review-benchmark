import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from tools.compare_judges import compare
import hashlib

class ComparisonTests(unittest.TestCase):
    def test_same_answer_and_reject_changed_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); runs=[root/'a',root/'b']
            dataset=b'{}\n'
            case=SimpleNamespace(safe_id='case',instance_id='case')
            suite=SimpleNamespace(load_cases=lambda _: [case])
            for run in runs:
                (run/'results').mkdir(parents=True);(run/'metrics').mkdir()
                (run/'dataset.jsonl').write_bytes(dataset)
                (run/'run-config.json').write_text(json.dumps({'dataset_sha256':hashlib.sha256(dataset).hexdigest(),'judge_model':'same'}))
                (run/'results/case.json').write_text(json.dumps({'status':'completed','review':{'comments':[]}}))
                (run/'metrics/metrics_ocr_average.json').write_text(json.dumps({'summary':{'total_instances':1,'evaluated_instances':1,'semantic_match_rate':0,'semantic_recall_rate':0,'semantic_f1':0},'eval_rounds':3}))
            with patch('tools.compare_judges.resolve_run_suite',return_value=suite),patch('tools.compare_judges.validate_answer',side_effect=lambda c,p:json.loads(p.read_text())):
                compare(*runs,root/'comparison.md')
                self.assertTrue((root/'comparison.json').exists())
                (runs[1]/'results/case.json').write_text(json.dumps({'changed':True}))
                with self.assertRaisesRegex(ValueError,'答卷不一致'):
                    compare(*runs,root/'second.md')

if __name__=='__main__': unittest.main()
