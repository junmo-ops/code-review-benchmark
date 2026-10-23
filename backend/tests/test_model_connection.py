import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from webui.service import Dashboard
from webui.profiles import test_connection
from webui.wsgi import create_app

HTTP_CLIENT = httpx.Client


def probe_client(requests, response=None, error=None):
    def handle(request):
        requests.append(request)
        if error:
            raise error
        return response
    return lambda **kwargs: HTTP_CLIENT(transport=httpx.MockTransport(handle), **kwargs)


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'BENCH_CONFIG_ROOT':self.tmp.name,'BENCH_DATA_ROOT':self.tmp.name,'BENCH_PUBLIC_URL':'https://review.test','BENCH_PROXY_TOKEN':'p'*40,'BENCH_CSRF_TOKEN':'c'*40})
        self.env.start();self.dashboard=Dashboard(Path(self.tmp.name))
        self.data=dict(provider='qwen',name='test',model='qwen-test',base_url='https://model.test/v1',api_key='secret-review',judge_source='custom',judge_model='judge',judge_base_url='https://judge.test/v1',judge_api_key='secret-judge')
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    def run_probe(self,response=None,error=None):
        requests=[]
        with patch('httpx.Client',side_effect=probe_client(requests,response,error)) as cls:
            result=test_connection(self.dashboard,self.data)
            self.assertEqual(len(requests),1)
            self.assertEqual(cls.call_args.kwargs,dict(timeout=20,follow_redirects=False))
            for request in requests:
                self.assertEqual(request.method,'POST')
                self.assertEqual(str(request.url),'https://model.test/v1/chat/completions')
                self.assertEqual(request.headers['Authorization'],'Bearer secret-review')
                payload=json.loads(request.content);self.assertEqual(payload['max_tokens'],32)
                self.assertEqual(payload['stream'],self.data.get('stream')=='true');self.assertNotIn('tools',payload)
            self.assertNotIn('secret-',json.dumps(result))
            self.assertEqual(list(Path(self.tmp.name).glob('model*.json')),[])
            return result
    def test_success_requires_complete_model_text(self):
        r=self.run_probe(httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'OK'}}]}))
        self.assertTrue(all(x['status']=='passed' for x in r['results']))
        r=self.run_probe(httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':None,'reasoning_content':'secret-review'}}]}))
        self.assertTrue(all(x['status']=='reachable' for x in r['results']))
    def test_errors_are_sanitized(self):
        for response in [httpx.Response(401,text='secret-review'),httpx.Response(429,text='secret-judge'),httpx.Response(200,json={'not_choices':'secret-review'})]:
            r=self.run_probe(response);self.assertTrue(all(x['status']=='failed' for x in r['results']))
        r=self.run_probe(error=httpx.ReadTimeout('secret-review'))
        self.assertIn('超时',r['results'][0]['message'])
    def test_authorization_and_saved_profile(self):
        config=dict(LCR_LLM_BASE_URL='https://model.test/v1',LCR_LLM_MODEL='qwen-test',LCR_LLM_TOKEN='secret-review',JUDGE_BASE_URL='https://judge.test/v1',JUDGE_MODEL='judge',JUDGE_API_KEY='secret-judge')
        (Path(self.tmp.name)/'model.test.local.json').write_text(json.dumps(config))
        client=create_app(self.dashboard).test_client()
        headers={'X-Bench-Proxy-Token':'p'*40,'X-Bench-User':'alice','X-Bench-Role':'viewer','X-Dashboard-Token':'c'*40,'Origin':'https://review.test'}
        response=httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'OK'}}]})
        requests=[]
        with patch('httpx.Client',side_effect=probe_client(requests,response)) as cls:
            def post():return client.post('/api/profiles/test',base_url='https://review.test',headers=headers,json={'profile_id':'model.test.local.json'})
            self.assertEqual(post().status_code,403);cls.assert_not_called()
            headers['X-Bench-Role']='operator';headers['Origin']='https://evil.test'
            self.assertEqual(post().status_code,403);cls.assert_not_called()
            headers['Origin']='https://review.test'
            response=post();self.assertEqual(response.status_code,200)
            self.assertEqual(len(requests),1)
            self.assertEqual(response.json['results'][0]['status'],'passed')
            self.assertNotIn('secret-',response.text)
        with self.assertRaises(ValueError):test_connection(self.dashboard,{'profile_id':'../../secret'})
