import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from reviewers.server_agent import adapter as a
from runner.cloud import checked_instances
from runner.storage import ROOT, save
from tools.mock_review_service import make_server


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run = self.root/'run'
        self.run.mkdir()
        self.config = json.loads((ROOT/'agent.example.json').read_text())
        self.config['poll_seconds'] = .01
        self.path = self.root/'agent.json'
        save(self.path, self.config)
        self.env = patch.dict(os.environ, {'BENCH_AGENT_CONFIG': str(self.path), 'LCR_LLM_MODEL': 'test-model', 'BENCH_AGENT_TOKEN': 'test-secret'})
        self.env.start()
        self.item = checked_instances(ROOT/'data/pilot1.jsonl', 'aacr')[0]
        self.cfg = {'timeout_minutes': 1, 'review_model': 'test-model'}
        a.configure(self.cfg, self.run)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def result(self):
        return dict(task_id='task', status='succeeded', complete=True, simulation=True,
            scope=dict(repository='benchmark/cline', base_commit=self.item.base_commit, head_commit=self.item.head_commit, diff_mode='merge_base'),
            model='test-model', engine_version='test', failed_files=0, comments=[], usage=None)

    def test_result_rejects_wrong_scope_partial_wrong_model(self):
        for key, value in [('model', 'other'), ('complete', False), ('failed_files', 1), ('simulation', False), ('scope', {})]:
            data=self.result();data[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError):a.convert(self.item,data,'task')
        self.assertEqual(a.convert(self.item,self.result(),'task')['review']['comments'], [])

    def test_no_gold_or_provider_key_in_request_and_redaction(self):
        self.assertEqual(set(a.payload(self.item)), {'repository','base_commit','head_commit','diff_mode','model'})
        self.assertEqual(a.redact('test-secret'), '[REDACTED]')

    def test_ambiguous_create_never_reposted(self):
        with patch.object(a,'request',side_effect=ValueError('lost response')) as req:
            with self.assertRaises(ValueError):a.run_case(self.item,self.run)
            with self.assertRaises(ValueError):a.run_case(self.item,self.run)
            with self.assertRaises(ValueError):a.run_case(self.item,self.run,resume=True)
            self.assertEqual(req.call_count,1)
        self.assertTrue((self.run/'raw'/f'{self.item.safe_id}.remote.json').exists())

    def test_resume_uses_get_and_simulation_never_formal_answer(self):
        save(self.run/'raw'/f'{self.item.safe_id}.remote.json',dict(task_id='task',request=a.payload(self.item)))
        with patch.object(a,'request',side_effect=[dict(status='succeeded'),self.result()]) as req:
            a.run_case(self.item,self.run,resume=True)
            self.assertEqual([c.args[0] for c in req.call_args_list],['GET','GET'])
        self.assertFalse((self.run/'results').exists())
        self.assertTrue((self.run/'raw'/f'{self.item.safe_id}.simulation.json').exists())

    def test_partial_status_stops_before_result(self):
        with patch.object(a,'request',side_effect=[dict(task_id='task'),dict(status='partial')]) as req:
            with self.assertRaises(ValueError):a.run_case(self.item,self.run)
            self.assertEqual(req.call_count,2)
        self.assertFalse((self.run/'results').exists())

    def test_wait_timeout_preserves_task_for_resume(self):
        def response(method,kind,*args,**kwargs):
            return dict(task_id='task') if kind=='create' else dict(status='running')
        with patch.object(a,'process_timeout',return_value=.02), patch.object(a,'request',side_effect=response):
            with self.assertRaisesRegex(ValueError,'本地等待超时'):a.run_case(self.item,self.run)
        record=json.loads((self.run/'raw'/f'{self.item.safe_id}.remote.json').read_text())
        execution=json.loads((self.run/'raw'/f'{self.item.safe_id}.execution.json').read_text())
        self.assertEqual(record['task_id'],'task')
        self.assertEqual(execution['status'],'remote_wait_timeout')
        self.assertFalse(execution['remote_stop_confirmed'])

    def test_mapped_result_fields_and_comments(self):
        data=self.result()
        data['comments']=[dict(path='a.ts',start_line=1,end_line=2,content='A defect')]
        a.CONFIG['result_fields']={key:'data.'+key for key in data}
        result=a.convert(self.item,dict(data=data),'task')
        self.assertEqual(result['review']['comments'][0]['content'],'A defect')

    def test_real_result_preserves_unknown_usage(self):
        a.CONFIG['simulation']=False
        data=self.result();data['simulation']=False
        with patch.object(a,'request',side_effect=[dict(task_id='task'),dict(status='succeeded'),data]):
            a.run_case(self.item,self.run)
        answer=json.loads((self.run/'results'/f'{self.item.safe_id}.json').read_text())
        self.assertEqual(answer['review']['summary'],{})
        self.assertFalse(answer['service']['simulation'])

    def test_workbench_remote_command_avoids_local_git_and_runtime(self):
        from webui.service import Dashboard
        with patch.dict(os.environ, {'BENCH_DATA_ROOT':str(self.root),'BENCH_CONFIG_ROOT':str(self.root/'configs')}):
            app=Dashboard(ROOT,load_jobs=False)
            spec=dict(reviewer='server_agent',suite='aacr',dataset='pilot1.jsonl',evaluator='martian',timeout_minutes=1)
            cmd,env,limit=app.command('test','init','environment',init=spec)
            self.assertIn(str(ROOT/'cloud.py'),cmd)
            self.assertIn('server_agent',cmd)
            save(app.run_dir('test')/'run-config.json',spec)
            cmd,env,limit=app.command('test','review','environment',task=self.item.instance_id)
            self.assertEqual(limit,150)
            self.assertIn('--external-timeout-seconds',cmd)

    def test_mock_cli_full_flow_and_scoring_guard(self):
        server=make_server(0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            self.config['base_url']=f'http://127.0.0.1:{server.server_port}'
            save(self.path,self.config)
            def command(stage,*extra):
                return subprocess.run([sys.executable,str(ROOT/'cloud.py'),stage,'--run-id','smoke','--runs-root',str(self.root/'runs'),*extra],cwd=ROOT,capture_output=True,text=True,timeout=30)
            for stage,extra in [('init',['--reviewer','server_agent','--dataset',str(ROOT/'data/pilot1.jsonl'),'--suite','aacr','--model','test-model','--timeout-minutes','1']),('check',[]),('preview',['--instance',self.item.instance_id]),('review',['--instance',self.item.instance_id,'--external-timeout-seconds','150']),('status',[])]:
                result=command(stage,*extra)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(len(server.tasks),1)
            result=command('eval')
            self.assertNotEqual(result.returncode,0)
            self.assertIn('模拟服务结果禁止评分',result.stdout+result.stderr)
            self.assertFalse((self.root/'runs/smoke/results').exists())
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
