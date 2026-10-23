import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from webui.service import Dashboard, save, ROOT
from webui.server import handler_for
from regression_fixtures import seed_datasets, frontend_environment

class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        seed_datasets(self.root)
        self.enterContext(patch.dict(os.environ, frontend_environment()))
        self.app=Dashboard(self.root)
        save(self.root/'model.local.json',{'LCR_LLM_MODEL':'test','LCR_LLM_TOKEN':'PRIVATE_TEST_KEY','JUDGE_MODEL':'judge','JUDGE_API_KEY':'PRIVATE_JUDGE_KEY'})
    def tearDown(self):
        self.app.shutdown();self.tmp.cleanup()
    def test_public_snapshot_never_contains_keys(self):
        value=json.dumps(self.app.snapshot());self.assertNotIn('PRIVATE_',value)
        self.assertEqual(self.app.redact('PRIVATE_TEST_KEY hello PRIVATE_JUDGE_KEY'),'[REDACTED] hello [REDACTED]')
    def test_traversal_and_symlink_escape_rejected(self):
        for key in ['../model.local.json','mcr/../../bad','/tmp/x','a/b','x;touch']:
            with self.assertRaises(ValueError):self.app.run_dir(key)
        (self.root/'runs').mkdir();(self.root/'runs/escape').symlink_to('/tmp')
        with self.assertRaises(ValueError):self.app.run_dir('escape')
    def test_artifact_allowlist_and_xss_stays_text(self):
        run=self.app.run_dir('sample');run.mkdir(parents=True)
        (run/'report.zh-CN.md').write_text('<script>alert(1)</script> PRIVATE_TEST_KEY')
        (run/'secret.json').symlink_to(self.root/'model.local.json')
        self.assertNotIn('secret.json',[r['path'] for r in self.app.artifacts('sample')])
        self.assertIn('[REDACTED]',self.app.artifact('sample','report.zh-CN.md')['text'])
        with self.assertRaises(ValueError):self.app.artifact('sample','../../model.local.json')
    def test_unknown_suite_dataset_or_action_rejected(self):
        for req in [dict(action='create',name='x',suite='unknown',dataset='pilot1.jsonl'),dict(action='create',name='x',suite='aacr',dataset='../model.local.json'),dict(action='create',name='x',suite='codefuse_cr',dataset='codefuse-pilot1.jsonl',evaluator='original')]:
            with self.assertRaises(ValueError):self.app.submit(req)
    def test_second_job_rejected_and_restart_does_not_resume(self):
        self.app.active='busy'
        with self.assertRaisesRegex(ValueError,'已有任务'):self.app.submit(dict(action='create',name='x',suite='aacr',dataset='pilot1.jsonl'))
        self.app.active=None
        save(self.app.state/'jobs/old.json',dict(id='old',status='running',created_at='x'))
        restarted=Dashboard(self.root);self.assertEqual(restarted.jobs['old']['status'],'interrupted');self.assertIsNone(restarted.active)
    def test_existing_scores_do_not_rerun_eval(self):
        save(self.app.run_dir('old')/'run-config.json', {'reviewer':'lcr'})
        with patch.object(self.app,'cases',return_value=[{'id':'case'}]),patch.object(self.app,'summary',return_value={'f1':0}):
            steps=self.app.plan(dict(key='old',action='workflow'),{},None)
            self.assertEqual([s[0] for s in steps],['check','review'])
            with self.assertRaises(ValueError):self.app.plan(dict(key='old',action='eval'),{},None)
    def test_commands_are_arguments_not_shell_and_config_explicit(self):
        command,env,limit=self.app.command('mcr/test','init','model.local.json',init=dict(dataset='mcr-pilot1.jsonl',reviewer='llm_direct',max_tokens=32768))
        self.assertIsInstance(command,list);self.assertNotIn('PRIVATE_TEST_KEY',' '.join(command));self.assertEqual(env['LCR_LLM_TOKEN'],'PRIVATE_TEST_KEY')
        self.assertIn('--config',command)
    def test_failed_step_stops_workflow_and_log_persists(self):
        command=[sys.executable,'-u','-c',"print('PRIVATE_TEST_KEY');raise SystemExit(7)"]
        with patch.object(self.app,'plan',return_value=[('check',None,None),('review','case',None)]),patch.object(self.app,'command',return_value=(command,dict(os.environ),10)) as call:
            result=self.app.submit(dict(action='create',name='test',suite='aacr',dataset='pilot1.jsonl'))
            self.app.worker.join(5)
            self.assertEqual(call.call_count,1);self.assertEqual(self.app.jobs[result['job_id']]['status'],'failed')
            log=self.app.logs(result['job_id'])['text'];self.assertIn('[REDACTED]',log);self.assertNotIn('PRIVATE_',log)
    def test_cancel_cleans_detached_descendant(self):
        pidfile=self.root/'child.pid'
        code="import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); pathlib.Path("+repr(str(pidfile))+").write_text(str(p.pid)); time.sleep(60)"
        with patch.object(self.app,'command',return_value=([sys.executable,'-u','-c',code],dict(os.environ),70)):
            result=self.app.submit(dict(action='create',name='canceltest',suite='aacr',dataset='pilot1.jsonl'))
            deadline=time.monotonic()+5
            while not pidfile.exists() and time.monotonic()<deadline:time.sleep(.05)
            self.assertTrue(pidfile.exists());self.app.stop(result['job_id']);self.app.worker.join(12)
            self.assertFalse(self.app.worker.is_alive());self.assertEqual(self.app.jobs[result['job_id']]['status'],'cancelled')
            status=subprocess.run(['ps','-p',pidfile.read_text(),'-o','stat='],capture_output=True,text=True).stdout.strip()
            self.assertTrue(not status or status.startswith('Z'),status)
    def test_http_requires_token_and_same_origin(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.app,'test-token'))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for path,expected in [('/models',200),('/comparisons/',200),('/experiments',200),('/jobs',200),('/guide',200),('/missing.js',404),('/api/missing',404)]:
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
                conn.request('GET',path,headers={'X-Dashboard-Token':'test-token'})
                response=conn.getresponse();self.assertEqual(response.status,expected)
                body=response.read()
                if expected==200:
                    self.assertIn(b'id="root"',body)
                    self.assertNotIn(b'__STYLE_NONCE__',body)
                    self.assertIn("script-src 'self'",response.getheader('Content-Security-Policy'))
                conn.close()
            for headers,expected in [({},403),({'X-Dashboard-Token':'test-token','Origin':'http://evil.example'},403),({'X-Dashboard-Token':'test-token'},200)]:
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
                conn.request('GET','/api/state',headers=headers);response=conn.getresponse();self.assertEqual(response.status,expected);response.read();conn.close()
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
            conn.request('GET','/api/session');response=conn.getresponse()
            self.assertEqual(response.status,200)
            self.assertEqual(json.loads(response.read())['csrf_token'],'test-token');conn.close()
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port);conn.request('POST','/api/jobs',body='{}',headers={'Content-Type':'application/json'})
            response=conn.getresponse();self.assertEqual(response.status,403);response.read();conn.close()
        finally:server.shutdown();server.server_close();thread.join()
if __name__=='__main__':unittest.main()
