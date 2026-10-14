"""Simulate one synchronous platform command, with a fresh child and bounded lifetime."""
from runner.runtime import data_root, config_root, docker_context

from pathlib import Path
import argparse,datetime,json,os,signal,subprocess,sys,time
from runner.paths import PROJECT_ROOT as ROOT
ALLOWED={'LCR_LLM_BASE_URL','LCR_LLM_TOKEN','LCR_LLM_MODEL','LCR_LLM_STREAM','JUDGE_BASE_URL','JUDGE_API_KEY','JUDGE_MODEL','JUDGE_USE_MOCK'}

def process_parents():
    if sys.platform.startswith('linux'):
        pairs=[]
        for entry in Path('/proc').iterdir():
            if not entry.name.isdecimal():continue
            try:
                # The command name may contain spaces or parentheses.
                fields=(entry/'stat').read_text().rsplit(')',1)[1].split()
                pairs.append((int(entry.name),int(fields[1])))
            except (OSError,ValueError,IndexError):
                continue  # A child may exit while the process tree is sampled.
        return pairs
    listing=subprocess.run(['ps','-axo','pid=,ppid='],capture_output=True,text=True,check=True)
    return [tuple(map(int,line.split())) for line in listing.stdout.splitlines() if line.strip()]

def descendants(pid):
    pairs=process_parents()
    found={pid}
    while True:
        expanded=found | {child for child,parent in pairs if parent in found}
        if expanded==found:return found
        found=expanded

def terminate(process):
    # The review adapter gives LCR its own session, so killing only the outer
    # process group would leave that child alive. Track this invocation's tree.
    if os.name == 'nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=15,check=False)
        if process.poll() is None:process.kill()
        process.wait()
        return
    tracked=descendants(process.pid)
    try:os.killpg(process.pid,signal.SIGINT)
    except ProcessLookupError:pass
    try:process.wait(timeout=5)
    except subprocess.TimeoutExpired:tracked |= descendants(process.pid)
    for pid in sorted(tracked,reverse=True):
        try:
            if os.getpgid(pid)==pid:os.killpg(pid,signal.SIGKILL)
            else:os.kill(pid,signal.SIGKILL)
        except ProcessLookupError:pass
    process.wait()

def execute(command,env,limit,record,cwd=ROOT):
    stopped=[];prior={}
    def stop(sig,frame):stopped.append(sig)
    for sig in (signal.SIGINT,signal.SIGTERM,getattr(signal,'SIGHUP',None)):
        if sig is None:continue
        prior[sig]=signal.signal(sig,stop)
    begin=time.monotonic();process=None;status='starting';exit_code=None
    try:
        options=({'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True})
        process=subprocess.Popen(command,cwd=cwd,env=env,**options)
        while process.poll() is None:
            if stopped:status='interrupted';terminate(process);break
            if time.monotonic()-begin>=limit:status='platform_timeout';terminate(process);break
            time.sleep(.1)
        if status=='starting':status='exited'
        exit_code=process.returncode
    finally:
        if process is not None and process.poll() is None:terminate(process)
        for sig,handler in prior.items():signal.signal(sig,handler)
        record.parent.mkdir(parents=True,exist_ok=True)
        record.write_text(json.dumps({'status':status,'exit_code':exit_code,'elapsed_seconds':round(time.monotonic()-begin,3),'platform_timeout_seconds':limit,'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat()},indent=2)+'\n')
    return 130 if status=='interrupted' else 124 if status=='platform_timeout' else exit_code

def model_environment(args, p):
    env=dict(os.environ)
    cfg=Path(args.config) if args.config else config_root(ROOT)/'model.local.json'
    if not cfg.is_absolute():cfg=ROOT/cfg
    if args.config and not cfg.is_file():p.error('指定的模型配置文件不存在')
    if cfg.exists():
        try:values=json.loads(cfg.read_text(encoding='utf-8'))
        except (ValueError,OSError):p.error('模型配置无法读取或 JSON 格式无效')
        if not isinstance(values,dict) or set(values)-ALLOWED or any(not isinstance(v,str) for v in values.values()):p.error('model.local.json 字段或类型无效')
        env.update(values)
    env.setdefault('JUDGE_USE_MOCK','false')
    return env

def configure_stage(args, rest, env, p):
    if args.stage=='init' and '--model' not in rest:
        model=env.get('LCR_LLM_MODEL')
        if not model:p.error('请在 model.local.json 或环境变量中配置 LCR_LLM_MODEL')
        rest+=['--model',model]
    if args.stage=='review':
        if '--external-timeout-seconds' in rest:p.error('平台时限由 --timeout-seconds 统一设置')
        rest+=['--external-timeout-seconds',str(args.timeout_seconds)]

def main():
    p=argparse.ArgumentParser();p.add_argument('--timeout-seconds',type=int,required=True)
    p.add_argument('--config',help='选择本地模型配置；省略时使用 model.local.json')
    p.add_argument('stage',choices=['init','check','status','next','preview','review','recover','eval','report','export'])
    args,rest=p.parse_known_args()
    if args.timeout_seconds<1:p.error('超时必须大于0')
    if not os.getenv('BENCH_SERVER_MODE') and not (ROOT/'local-runtime.json').exists():p.error('请先完成本机 runtime 准备')
    env = model_environment(args, p)
    configure_stage(args, rest, env, p)
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    record=data_root(ROOT)/'local-command-logs'/(stamp+'.json')
    print('本次同步命令时限：',args.timeout_seconds,'秒；执行记录：',record,flush=True)
    python=ROOT/'.venv'/'Scripts'/'python.exe' if os.name=='nt' else ROOT/'.venv'/'bin'/'python'
    if not python.is_file():p.error(f'找不到项目 Python 环境：{python}')
    code=execute([str(python),str(ROOT/'backend/src/cloud.py'),args.stage,*rest],env,args.timeout_seconds,record)
    raise SystemExit(code)
if __name__=='__main__':main()
