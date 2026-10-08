import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from webui.service import Dashboard, ROOT, save
from webui.experiment_view import case_evidence, submit_named, decorate
from webui.wsgi import create_app


class ExperimentViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.env=patch.dict('os.environ',{'BENCH_DATA_ROOT':str(self.root),'BENCH_CONFIG_ROOT':str(self.root/'configs'),'BENCH_PUBLIC_URL':'https://review.test','BENCH_PROXY_TOKEN':'p'*40,'BENCH_CSRF_TOKEN':'c'*40})
        self.env.start();self.app=Dashboard(ROOT,load_jobs=False)
        self.key='compare-test';self.group='compare-test-g1';self.control=self.app.run_dir(self.key);self.run=self.app.run_dir(self.group)
        self.control.mkdir(parents=True)
        data=(ROOT/'data/pilot1.jsonl').read_bytes();(self.control/'dataset.jsonl').write_bytes(data)
        row=json.loads(data);self.case=row['instance_id'];self.safe=self.case.replace('/','__')
        save(self.control/'experiment.json',dict(groups=[dict(key=self.group)],case_ids=[self.case],suite='aacr',case_timeout_seconds=600,judge_case_timeout_seconds=600,protocol='martian_one_to_one_v1'))
        save(self.run/'comparison-status'/f'{self.safe}.json',dict(status='review_failed',error='TimeoutError',review_seconds=605))
        save(self.run/'raw'/f'{self.safe}.execution.json',dict(pid=123))
        diag=self.run/'diagnostics/log';diag.parent.mkdir()
        (diag.parent/'diagnostics-test-123.jsonl').write_text('\n'.join(json.dumps(x) for x in [dict(event='llm_call_start',call_id=1,file='a.ts',task='review_filter_task'),dict(event='http_attempt_headers',call_id=1,http_status=0),dict(event='http_attempt_end',call_id=1,deadline_exceeded=True),dict(event='llm_call_end',call_id=1,context_error=True,duration_ms=180000),dict(event='llm_call_start',call_id=2,file='b.ts',task='main_task')]))
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    def test_timeout_evidence_distinguishes_pending_call_and_deadline(self):
        before=(self.run/'comparison-status'/f'{self.safe}.json').read_bytes()
        d=case_evidence(self.app,self.key,self.group,self.case)
        self.assertEqual(d['diagnostic']['deadline_calls'][0]['seconds'],180)
        self.assertEqual(d['diagnostic']['pending_calls'][0]['call_id'],2)
        self.assertEqual(d['diagnostic']['http_statuses'],{'0':1})
        self.assertEqual(d['comments'],[]);self.assertEqual(d['score'],{})
        self.assertEqual(before,(self.run/'comparison-status'/f'{self.safe}.json').read_bytes())
    def test_scoring_retains_decisions_and_selected_pairs(self):
        decisions=[dict(golden=0,candidate=0,match=True,reasoning='first'),dict(golden=1,candidate=0,match=True,reasoning='unselected')]
        save(self.run/'comparison-scores'/f'{self.safe}.json',dict(status='complete',metrics=dict(pairs=[[0,0]]),decisions=decisions))
        save(self.run/'results'/f'{self.safe}.json',dict(review=dict(comments=[dict(content='test',path='a',start_line=1)])))
        d=case_evidence(self.app,self.key,self.group,self.case)
        self.assertEqual(d['score']['metrics']['pairs'],[[0,0]])
        self.assertEqual(d['score']['decisions'],decisions)
        self.assertEqual(len(d['references']),2)
    def test_foreign_group_case_and_symlink_rejected(self):
        for group,case in [('elsewhere',self.case),(self.group,'../../model.local')]:
            with self.assertRaises(ValueError):case_evidence(self.app,self.key,group,case)
        folder=self.run/'results';folder.mkdir();(folder/f'{self.safe}.json').symlink_to(self.control/'experiment.json')
        with self.assertRaises(ValueError):case_evidence(self.app,self.key,self.group,self.case)
    def test_name_persisted_outside_frozen_manifest_and_validation_before_submit(self):
        original=(self.control/'experiment.json').read_bytes()
        with patch.object(self.app,'submit',return_value=dict(key=self.key,job_id='fake')) as submit:
            submit_named(self.app,dict(action='create',display_name='中文实验'))
            with self.assertRaises(ValueError):submit_named(self.app,dict(action='create',display_name='bad\nname'))
            self.assertEqual(submit.call_count,1)
        experiments=[dict(key=self.key,name='technical')]
        decorate(self.app,experiments,[])
        self.assertEqual(experiments[0]['display_name'],'中文实验')
        self.assertEqual(original,(self.control/'experiment.json').read_bytes())
    def test_evidence_endpoint_requires_auth_and_is_read_only(self):
        client=create_app(self.app).test_client();query=dict(key=self.key,group=self.group,case=self.case)
        headers={'X-Bench-Proxy-Token':'p'*40,'X-Bench-User':'viewer','X-Bench-Role':'viewer','X-Dashboard-Token':'c'*40}
        self.assertEqual(client.get('/api/comparison-case',base_url='https://review.test',query_string=query).status_code,403)
        r=client.get('/api/comparison-case',base_url='https://review.test',headers=headers,query_string=query)
        self.assertEqual(r.status_code,200);self.assertEqual(r.json['status']['error'],'TimeoutError')
        self.assertEqual(client.post('/api/comparison-case',base_url='https://review.test',headers=headers,json=query).status_code,403)

if __name__=='__main__':unittest.main()
