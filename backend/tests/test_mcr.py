import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from multiround import core,cli
from runner.storage import ROOT,save

class MCRTests(unittest.TestCase):
    def setUp(self):
        self.cases=core.load(ROOT/'data/mcr-pilot1.jsonl');self.row=self.cases[0]['rounds'][0]
        self.card={'task_id':self.row['task_id'],'round_id':1,'defects':[{'defect_id':'d1','title':'problem','description':'details','state_transition':{'current_state':'New','previous_state':'NotPresent','transition_reason':'introduced'}}]}
    def test_real_frozen_data_is_three_prior_history_rounds(self):
        self.assertEqual(len(self.cases[0]['rounds']),3)
        for r in self.cases[0]['rounds']:
            self.assertTrue(all(x.get('round_id',0)<r['round_id'] for x in r['history']))
    def test_future_history_rejected(self):
        cases=copy.deepcopy(self.cases);cases[0]['rounds'][0]['history'].append({'round_id':2})
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'data.jsonl';p.write_text(json.dumps(cases[0]))
            with self.assertRaisesRegex(ValueError,'未来'):core.load(p)
    def test_duplicates_and_wrong_states_are_not_valid_cards(self):
        bad=copy.deepcopy(self.card);bad['defects']*=2
        with self.assertRaises(ValueError):core.validate_card(bad,self.row)
        bad=copy.deepcopy(self.card);bad['defects'][0]['state_transition']=None
        with self.assertRaises((ValueError,AttributeError)):core.validate_card(bad,self.row)
        core.validate_card(bad,self.row,'lcr')
    def test_one_to_one_match_enforced(self):
        with self.assertRaisesRegex(ValueError,'一对一'):
            core.matching_pairs({'matches':[{'model_defect_index':1,'ground_truth_defect_index':1},{'model_defect_index':2,'ground_truth_defect_index':1}]},[{}],[{},{}])
    def test_defect_match_is_distinct_from_state_accuracy(self):
        refs=[{'state':'Resolved','previous_state':'New'}];preds=self.card['defects']
        m=core.metrics(refs,preds,[(0,0)]);self.assertEqual(m['f1'],1);self.assertEqual(m['state_accuracy'],0)
        m=core.metrics(refs,[{'state_transition':None}],[(0,0)])
        self.assertIsNone(m['state_accuracy']);self.assertEqual(m['state_coverage'],0)
        self.assertEqual(m['tp'],1)
    def test_failed_attempt_retained_requires_explicit_retry(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'review';p.mkdir();(p/'request.json').write_text('{}')
            with self.assertRaises(ValueError):cli.start_attempt(p,False)
            cli.start_attempt(p,True);self.assertFalse(p.exists());self.assertEqual(len(list((p.parent/'attempts').rglob('request.json'))),1)
    def test_partial_report_has_no_aggregate_score(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);folder=root/'rounds'/self.row['task_id'];folder.mkdir(parents=True);save(folder/'answer.json',self.card)
            cli.report(self.cases,{'reviewer':'llm_direct','review_model':'test','judge_model':'test'},root)
            summary=core.read(root/'metrics.json')['summary'];self.assertEqual(summary['status'],'partial');self.assertNotIn('f1',summary)
    def test_complete_round_skipped_and_tampered_input_never_sent(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);folder=root/'rounds'/self.row['task_id'];folder.mkdir(parents=True);save(folder/'answer.json',self.card)
            args=SimpleNamespace(task=self.row['task_id'],retry=False)
            with patch('multiround.cli.check_model'),patch('multiround.cli.call') as call:
                cli.review(self.cases,{'reviewer':'llm_direct'},root,args);call.assert_not_called()
                (folder/'answer.json').unlink();save(folder/'input.json',{'messages':[{'role':'user','content':'ground truth injected'}]})
                with self.assertRaisesRegex(ValueError,'输入'):cli.review(self.cases,{'reviewer':'llm_direct'},root,args)
                call.assert_not_called()
if __name__=='__main__':unittest.main()
