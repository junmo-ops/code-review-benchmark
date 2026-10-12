import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from webui.service import Dashboard
from webui.profiles import create_profile
from webui.wsgi import create_app

class ProfileApiTests(unittest.TestCase):
    def test_write_only_profiles_and_access(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'BENCH_CONFIG_ROOT':tmp,'BENCH_DATA_ROOT':tmp,'BENCH_PUBLIC_URL':'https://review.test','BENCH_PROXY_TOKEN':'p'*40,'BENCH_CSRF_TOKEN':'c'*40}):
            dashboard=Dashboard(Path(tmp));client=create_app(dashboard).test_client()
            headers={'X-Bench-Proxy-Token':'p'*40,'X-Bench-User':'alice','X-Bench-Role':'viewer','X-Dashboard-Token':'c'*40,'Origin':'https://review.test'}
            data=dict(provider='qwen',name='内网 Qwen',model='qwen-test',base_url='http://model.test/v1',api_key='private-test-token',judge_source='custom',judge_model='judge',judge_base_url='https://judge.test/v1',judge_api_key='private-judge-token')
            def post():return client.post('/api/profiles',base_url='https://review.test',headers=headers,json=data)
            self.assertEqual(post().status_code,403)
            headers['X-Bench-Role']='operator';headers['Origin']='https://bad.test'
            self.assertEqual(post().status_code,403);headers['Origin']='https://review.test'
            reply=post();self.assertEqual(reply.status_code,201)
            self.assertRegex(reply.json['id'],r'^model\.qwen\.[a-f0-9]{32}\.local\.json$')
            self.assertEqual(dashboard.profiles()[0]['label'],'内网 Qwen')
            path=Path(tmp)/reply.json['id'];before=path.read_bytes()
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertEqual(Dashboard(Path(tmp)).profiles()[0]['label'],'内网 Qwen')
            self.assertNotIn('内网 Qwen',json.dumps(dashboard.environment(reply.json['id']),ensure_ascii=False))
            self.assertEqual(post().status_code,400);self.assertEqual(path.read_bytes(),before)
            data.update(provider='deepseek',name='cloud',judge_source=reply.json['id'])
            second=post();self.assertEqual(second.status_code,201)
            self.assertNotIn('JUDGE_API_KEY',json.loads((Path(tmp)/second.json['id']).read_text()))
            self.assertNotIn('private-',json.dumps(dashboard.profiles()))
            for name,url in [('bad\nname','https://model.test'),('bad','https://user:pass@model.test'),('bad','https://model.test/v1/chat/completions')]:
                data.update(name=name,base_url=url)
                self.assertEqual(post().status_code,400)
            self.assertEqual(len(list(Path(tmp).glob('model*.json'))),2)
