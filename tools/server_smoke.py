"""Real Web/Worker process smoke, isolated data; only init, no model calls."""
import json,os,secrets,signal,socket,subprocess,sys,tempfile,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local import terminate

def main():
    with tempfile.TemporaryDirectory(prefix='review-server-') as temp:
        base=Path(temp);cfg=base/'config';cfg.mkdir()
        (cfg/'model.local.json').write_text(json.dumps(dict(LCR_LLM_MODEL='smoke-no-network',LCR_LLM_TOKEN='test-only',LCR_LLM_BASE_URL='https://invalid.example',JUDGE_MODEL='smoke-no-network',JUDGE_API_KEY='test-only',JUDGE_BASE_URL='https://invalid.example')))
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        proxy=secrets.token_urlsafe(40);csrf=secrets.token_urlsafe(40)
        env=dict(os.environ,BENCH_SERVER_MODE='1',BENCH_DATA_ROOT=str(base/'data'),BENCH_CONFIG_ROOT=str(cfg),BENCH_PUBLIC_URL='https://review.test',BENCH_PROXY_TOKEN=proxy,BENCH_CSRF_TOKEN=csrf)
        env.pop('GUNICORN_CMD_ARGS',None)
        processes=[];worker=None
        def launch(command,name):
            with (base/(name+'.log')).open('ab') as f:
                p=subprocess.Popen(command,cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
            processes.append(p);return p
        def web():return launch([str(ROOT/'.venv/bin/gunicorn'),'--bind',f'127.0.0.1:{port}','--workers','1','webui.wsgi:create_app()'],'web')
        def api(path,data=None):
            req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=json.dumps(data).encode() if data else None,headers={'Host':'review.test','Origin':'https://review.test','X-Bench-Proxy-Token':proxy,'X-Bench-User':'smoke','X-Bench-Role':'operator','X-Dashboard-Token':csrf,'Content-Type':'application/json'})
            return json.load(urllib.request.urlopen(req,timeout=10))
        def ready():
            for _ in range(100):
                try:return api('/api/state')
                except OSError:time.sleep(.1)
            raise RuntimeError('Web did not start')
        try:
            worker=launch([sys.executable,'-m','webui.worker'],'worker');server=web();ready()
            # Keep queued job unclaimed while restarting the real Web process.
            os.kill(worker.pid,signal.SIGSTOP)
            job=api('/api/jobs',dict(action='create',suite='aacr',dataset='pilot1.jsonl',name='server-smoke',evaluator='original',profile='model.local.json'))
            terminate(server);server=web();s=ready()
            assert next(j for j in s['jobs'] if j['id']==job['job_id'])['status']=='queued'
            os.kill(worker.pid,signal.SIGCONT)
            for _ in range(100):
                s=api('/api/state');j=next(j for j in s['jobs'] if j['id']==job['job_id'])
                if j['status'] in ('succeeded','failed'):break
                time.sleep(.1)
            assert j['status']=='succeeded',j
            run=base/'data/runs/server-smoke/run-config.json';frozen=json.loads(run.read_text())
            assert frozen['runtime_mode']=='server'
            assert frozen['repos_root']==str((base/'data/repos').resolve())
            assert not (run.parent/'raw').exists()
            result=dict(status='passed',real_web_worker=True,queue_survived_web_restart=True,linux_runtime_marker_not_required=True,external_data_config=True,model_calls=0,platform=sys.platform)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            (ROOT/'ui-state/server-validation.json').write_text(json.dumps(result,indent=2)+'\n')
        except BaseException:
            for log in base.glob('*.log'):print(log.name,log.read_text()[-5000:])
            raise
        finally:
            if worker and worker.poll() is None:os.kill(worker.pid,signal.SIGCONT)
            for p in reversed(processes):
                if p.poll() is None:terminate(p)
if __name__=='__main__':main()
