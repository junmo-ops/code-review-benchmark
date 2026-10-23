import json
import http.client
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from webui.queue import DurableDashboard
from webui.service import save
from webui.wsgi import create_app
from webui.server import handler_for
from http.server import ThreadingHTTPServer
from regression_fixtures import seed_datasets, frontend_environment

class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        self.env=patch.dict(os.environ,{**frontend_environment(),'BENCH_DATA_ROOT':str(self.root/'persistent'),'BENCH_CONFIG_ROOT':str(self.root/'config'),'BENCH_SERVER_MODE':'1','BENCH_PUBLIC_URL':'https://review.test','BENCH_PROXY_TOKEN':'p'*40,'BENCH_CSRF_TOKEN':'c'*40})
        self.env.start();self.app=DurableDashboard(self.root)
        seed_datasets(self.root)
        save(self.root/'config/model.local.json',dict(LCR_LLM_MODEL='test',LCR_LLM_TOKEN='secret-do-not-store',LCR_LLM_BASE_URL='https://model.test',JUDGE_MODEL='test',JUDGE_API_KEY='judge-secret'))
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    def submit(self,name='one'):
        return self.app.submit(dict(action='create',suite='aacr',dataset='pilot1.jsonl',name=name))
    def test_durable_claim_duplicate_and_cancel(self):
        a=self.submit();b=self.submit('two')
        with self.assertRaises(ValueError):self.submit()
        restarted=DurableDashboard(self.root)
        job,_,_=restarted.queue.claim();self.assertEqual(job['id'],a['job_id'])
        self.assertEqual(DurableDashboard(self.root).jobs[a['job_id']]['status'],'running')
        restarted.stop(b['job_id']);self.assertIsNone(restarted.queue.claim())
        self.assertEqual(restarted.queue.jobs()[b['job_id']]['status'],'cancelled')
        self.assertNotIn('secret-do-not-store',json.dumps(restarted.queue.jobs()))
    def test_worker_recovery_never_requeues(self):
        a=self.submit();self.app.queue.claim();self.app.queue.recover()
        self.assertEqual(self.app.queue.jobs()[a['job_id']]['status'],'interrupted')
        self.assertTrue(self.app.queue.meta('recovery_required'));self.assertIsNone(self.app.queue.claim())
    def test_local_recovery_confirmation_unblocks_without_replaying_interrupted_job(self):
        submitted = self.submit()
        self.app.queue.claim()
        self.app.queue.recover()
        with ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.app, 'local-token')) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for confirmed, expected in ((False, 400), (True, 200)):
                    connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
                    connection.request('POST', '/api/recovery', json.dumps(dict(confirmed=confirmed)),
                        {'Content-Type': 'application/json', 'X-Dashboard-Token': 'local-token'})
                    response = connection.getresponse()
                    response.read()
                    self.assertEqual(response.status, expected)
                    connection.close()
            finally:
                server.shutdown()
                thread.join(timeout=2)
        self.assertFalse(self.app.queue.meta('recovery_required'))
        self.assertEqual(self.app.queue.jobs()[submitted['job_id']]['status'], 'interrupted')
        self.assertIsNone(self.app.queue.claim())
    def test_web_restart_does_not_kill_worker_and_cancel_persists(self):
        a=self.submit();job,request,spec=self.app.queue.claim()
        worker=DurableDashboard(self.root);worker.active=job['id']
        started=self.root/'started'
        cmd=[sys.executable,'-u','-c',f'import pathlib,time; pathlib.Path({str(started)!r}).touch(); print("working",flush=True); time.sleep(20)']
        with patch.object(worker,'command',return_value=(cmd,dict(os.environ),30)):
            thread=threading.Thread(target=worker.work,args=(job['id'],request,spec));thread.start()
            deadline=time.time()+5
            while not started.exists() and time.time()<deadline:time.sleep(.05)
            self.assertTrue(started.exists());self.app.shutdown()
            other=DurableDashboard(self.root);self.assertEqual(other.jobs[job['id']]['status'],'running')
            other.stop(job['id']);thread.join(12);self.assertFalse(thread.is_alive())
            self.assertEqual(other.queue.jobs()[job['id']]['status'],'cancelled')
            self.assertIn('working',other.logs(job['id'])['text'])
    def test_proxy_auth_roles_csrf_and_header_spoofing(self):
        app=create_app(self.app);client=app.test_client()
        headers={'X-Bench-Proxy-Token':'p'*40,'X-Bench-User':'alice','X-Bench-Role':'viewer','X-Dashboard-Token':'c'*40}
        self.assertEqual(client.get('/api/state',base_url='https://review.test').status_code,403)
        self.assertEqual(client.get('/api/state',base_url='https://review.test',headers=headers).status_code,200)
        request=dict(action='create',suite='aacr',dataset='pilot1.jsonl',name='http',_actor='forged')
        headers['Origin']='https://review.test'
        self.assertEqual(client.post('/api/jobs',base_url='https://review.test',headers=headers,json=request).status_code,403)
        headers['X-Bench-Role']='operator';headers['Origin']='https://evil.test'
        self.assertEqual(client.post('/api/jobs',base_url='https://review.test',headers=headers,json=request).status_code,403)
        headers['Origin']='https://review.test'
        reply=client.post('/api/jobs',base_url='https://review.test',headers=headers,json=request)
        self.assertEqual(reply.status_code,202)
        self.assertEqual(self.app.queue.jobs()[reply.json['job_id']]['submitted_by'],'alice')
    def test_history_routes_keep_auth_and_unknown_paths(self):
        client=create_app(self.app).test_client()
        headers={'X-Bench-Proxy-Token':'p'*40,'X-Bench-User':'alice','X-Bench-Role':'viewer','X-Dashboard-Token':'c'*40}
        for path in ['/models','/comparisons/','/experiments','/jobs','/guide']:
            self.assertEqual(client.get(path,base_url='https://review.test').status_code,403)
            reply=client.get(path,base_url='https://review.test',headers=headers)
            self.assertEqual(reply.status_code,200);self.assertIn(b'id="root"',reply.data)
            self.assertNotIn(b'__STYLE_NONCE__',reply.data)
            self.assertIn("script-src 'self'",reply.headers['Content-Security-Policy'])
        session=client.get('/api/session',base_url='https://review.test',headers=headers)
        self.assertEqual(session.status_code,200)
        self.assertEqual(session.json,dict(csrf_token='c'*40,access_role='viewer'))
        for path in ['/missing.js','/api/missing','/unknown/page']:
            self.assertEqual(client.get(path,base_url='https://review.test',headers=headers).status_code,404)

    def test_server_commands_use_external_config_and_data(self):
        cmd,env,_=self.app.command('new','init','model.local.json',init=dict(dataset='pilot1.jsonl',suite='aacr',reviewer='lcr',evaluator='martian',timeout_minutes=10))
        self.assertIn(str(self.root/'config/model.local.json'),cmd)
        self.assertEqual(env['BENCH_SERVER_MODE'],'1')
        self.assertEqual(self.app.run_dir('new'),self.root/'persistent/runs/new')
    def test_profile_mutation_changes_queue_fingerprint(self):
        job=self.submit();before=self.app.queue.jobs()[job['job_id']]['profile_fingerprint']
        p=self.root/'config/model.local.json';obj=json.loads(p.read_text());obj['LCR_LLM_MODEL']='changed';save(p,obj)
        self.assertNotEqual(before,self.app.fingerprint('model.local.json'))

if __name__=='__main__':unittest.main()
