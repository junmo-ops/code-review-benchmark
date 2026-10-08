"""Draft HTTP review-service contract. Only review inputs cross the boundary."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import time
import uuid
from urllib.parse import urlsplit, quote
import httpx
from runner.storage import ROOT, save, validate_answer
from runner.runtime import config_root

CAPABILITIES=frozenset({'git_range','findings'})
CONFIG=None
RUN=None
CFG=None
REPO=None


def load_config():
    path=Path(os.getenv('BENCH_AGENT_CONFIG',str(config_root(ROOT)/'agent.local.json')))
    c=json.loads(path.read_text(encoding='utf-8'))
    if c.get('contract')!='review-service-v1-draft':raise ValueError('服务端 Agent 协议版本无效')
    allowed={'contract','base_url','simulation','token_env','paths','repositories','diff_mode','poll_seconds','request_timeout_seconds','request_fields','response_fields','result_fields','comment_fields','status_values'}
    if set(c)-allowed:raise ValueError('Agent 配置包含未知字段；凭证只能通过环境变量设置')
    for name in ('request_fields','response_fields','result_fields','comment_fields','status_values'):
        m=c.get(name,{})
        if not isinstance(m,dict) or any(not isinstance(k,str) or not isinstance(v,str) or not v for k,v in m.items()):raise ValueError('字段映射必须为非空字符串映射')
    u=urlsplit(c.get('base_url',''))
    if u.scheme not in ('http','https') or not u.netloc or u.username or u.password or u.query or u.fragment:raise ValueError('Agent base_url 无效')
    for k in ('create','status','result'):
        p=c.get('paths',{}).get(k,'')
        if not p.startswith('/') or p.startswith('//') or '?' in p or '#' in p or '..' in p:raise ValueError('Agent 路径无效')
        if k!='create' and '{task_id}' not in p:raise ValueError('查询路径需要 {task_id}')
    if not isinstance(c.get('repositories'),dict) or not c['repositories']:raise ValueError('需配置案例仓库到服务端仓库标识的映射')
    if any(not isinstance(k,str) or not isinstance(v,str) or not k or not v for k,v in c['repositories'].items()):raise ValueError('仓库映射必须为非空字符串')
    if c.get('diff_mode') not in ('merge_base','direct'):raise ValueError('明确配置 diff_mode')
    if type(c.get('simulation')) is not bool:raise ValueError('必须明确 simulation 开关')
    for name,default,maximum in [('poll_seconds',3,60),('request_timeout_seconds',20,60)]:
        v=c.get(name,default)
        if type(v) not in (int,float) or not 0.01<=v<=maximum:raise ValueError('Agent 请求时限或轮询间隔无效')
    env=c.get('token_env','BENCH_AGENT_TOKEN')
    if not isinstance(env,str) or not env.replace('_','').isalnum():raise ValueError('token_env 无效')
    if not c['simulation'] and not os.getenv(env):raise ValueError('请通过 '+env+' 配置服务鉴权，不发送模型 API Key')
    return c


def redact(text):
    try:secret=os.getenv(load_config().get('token_env','BENCH_AGENT_TOKEN'),'')
    except (OSError,ValueError):secret=os.getenv('BENCH_AGENT_TOKEN','')
    return str(text).replace(secret,'[REDACTED]') if secret else str(text)


def tool_identity():
    c=load_config()
    return {'contract':c['contract'],'config_sha256':hashlib.sha256(json.dumps(c,sort_keys=True).encode()).hexdigest(),
            'adapter_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def configure(cfg,run):
    global CONFIG,RUN,CFG
    CONFIG=load_config();RUN=run;CFG=cfg


def require_model(cfg):
    if os.getenv('LCR_LLM_MODEL')!=cfg['review_model']:raise ValueError('被测模型与批次不一致')


def check_repos(items):
    for item in items:
        if not CONFIG['repositories'].get(item.repo):raise ValueError('缺少服务端仓库映射：'+item.repo)
    print('仓库映射已配置；本地检查不证明服务端存在这些提交。结果必须回传实际范围。')


def process_timeout(item):return CFG['timeout_minutes']*60


def field(data,path):
    for part in path.split('.'):
        if not isinstance(data,dict) or part not in data:raise ValueError('服务响应缺少字段：'+path)
        data=data[part]
    return data


def payload(item):
    values=dict(repository=CONFIG['repositories'][item.repo],base_commit=item.base_commit,head_commit=item.head_commit,
                diff_mode=CONFIG['diff_mode'],model=CFG['review_model'])
    names=CONFIG.get('request_fields',{})
    result={names.get(k,k):v for k,v in values.items()}
    if len(result)!=len(values):raise ValueError('请求字段映射冲突')
    return result


def request(method,kind,task_id=None,body=None,key=None,remaining=20):
    if remaining<=0:raise ValueError('本地等待时限已到，远端任务未确认停止')
    path=CONFIG['paths'][kind].replace('{task_id}',quote(task_id or '',safe=''))
    headers={'Accept':'application/json'}
    token=os.getenv(CONFIG.get('token_env','BENCH_AGENT_TOKEN'),'')
    if token:headers['Authorization']='Bearer '+token
    if key:headers['Idempotency-Key']=key
    # No redirects, implicit retries, or model-provider credentials.
    try:
        with httpx.Client(timeout=min(CONFIG.get('request_timeout_seconds',20),max(.01,remaining)),follow_redirects=False,trust_env=False) as client:
            r=client.request(method,CONFIG['base_url'].rstrip('/')+path,headers=headers,json=body)
        if r.status_code not in (200,201,202):raise ValueError('Agent HTTP '+str(r.status_code))
        data=r.json()
        if not isinstance(data,dict):raise ValueError('Agent 响应必须为 JSON 对象')
        return data
    except httpx.HTTPError as exc:raise ValueError('Agent 网络请求失败：'+type(exc).__name__) from None


def convert(item,data,task_id):
    maps=CONFIG.get('result_fields',{})
    get=lambda key:field(data,maps.get(key,key))
    if get('task_id')!=task_id or CONFIG.get('status_values',{}).get(get('status'),get('status'))!='succeeded' or get('complete') is not True:raise ValueError('结果不完整或任务不匹配')
    if get('simulation') is not CONFIG['simulation']:raise ValueError('模拟/真实模式与服务结果不一致')
    actual=get('scope')
    expected=dict(repository=CONFIG['repositories'][item.repo],base_commit=item.base_commit,head_commit=item.head_commit,diff_mode=CONFIG['diff_mode'])
    if actual!=expected:raise ValueError('服务实际检视范围与案例不一致，不评分')
    if get('model')!=CFG['review_model']:raise ValueError('服务实际模型与请求不一致，不评分')
    version=get('engine_version')
    if not isinstance(version,str) or not version.strip():raise ValueError('结果缺少实际引擎版本')
    if get('failed_files')!=0 or type(get('failed_files')) is not int:raise ValueError('存在文件失败或失败数不明确，不评分')
    rows=get('comments')
    if not isinstance(rows,list):raise ValueError('comments 必须为数组，零意见用空数组')
    comments=[];cm=CONFIG.get('comment_fields',{})
    for c in rows:
        values={k:field(c,cm.get(k,k)) for k in ('path','start_line','end_line','content')}
        p=values['path'];start=values['start_line'];end=values['end_line'];text=values['content']
        if not isinstance(p,str) or not p or p.startswith(('/','\\')) or '\\' in p or ':' in p or '..' in PurePosixPath(p).parts or type(start) is not int or type(end) is not int or start<1 or end<start or not isinstance(text,str) or not text.strip():raise ValueError('检视意见位置或内容无效')
        comments.append(values)
    summary={}
    usage=get('usage')
    if usage is not None:
        if not isinstance(usage,dict):raise ValueError('usage 无效')
        for k in ('input_tokens','output_tokens','total_tokens'):
            v=usage.get(k)
            if v is not None and (type(v) is not int or v<0):raise ValueError('Token 用量无效')
            if v is not None:summary[k]=v
    return dict(instance_id=item.instance_id,repo=item.repo,base_commit=item.base_commit,head_commit=item.head_commit,reviewer='server_agent',
                review=dict(status='complete',comments=comments,summary=summary,llm={'model':get('model')}),
                service=dict(task_id=task_id,engine_version=version,scope=actual,simulation=CONFIG['simulation'],timing=data.get('timing')))


def run_case(item,run,preview=False,resume=False):
    raw=run/'raw';record_path=raw/(item.safe_id+'.remote.json');execution=raw/(item.safe_id+'.execution.json')
    if preview:
        save(run/'preview'/(item.safe_id+'.json'),payload(item));print('仅生成服务端请求预览，不创建任务：',item.instance_id);return
    if (run/'results'/(item.safe_id+'.json')).exists():validate_answer(item,run/'results'/(item.safe_id+'.json'));return
    started=time.monotonic();deadline=started+process_timeout(item)
    record=json.loads(record_path.read_text(encoding='utf-8')) if record_path.exists() else None
    if record and not resume:raise ValueError('已有服务任务记录。使用恢复查询，禁止重复创建；创建响应未知时请先找维护方核实幂等键。')
    if resume and (not record or not record.get('task_id')):raise ValueError('没有已知任务 ID，不能自动恢复或重新创建')
    if record and record.get('request')!=payload(item):raise ValueError('保存的请求与当前案例不一致，拒绝恢复')
    status='running';error=None
    save(execution,dict(status='running',task_id=(record or {}).get('task_id')))
    def event(kind,**values):
        raw.mkdir(parents=True,exist_ok=True)
        with (raw/(item.safe_id+'.events.jsonl')).open('a',encoding='utf-8') as f:
            f.write(json.dumps(dict(time=time.time(),event=kind,**values),ensure_ascii=False)+'\n')
    event('resume' if resume else 'create_intent')
    try:
        if not record:
            record=dict(idempotency_key=str(uuid.uuid4()),state='creating',request=payload(item))
            save(record_path,record)  # Persist before the potentially billable POST.
            data=request('POST','create',body=record['request'],key=record['idempotency_key'],remaining=deadline-time.monotonic())
            task_id=field(data,CONFIG.get('response_fields',{}).get('task_id','task_id'))
            if not isinstance(task_id,str) or not task_id or len(task_id)>200:raise ValueError('服务未返回有效任务 ID')
            record.update(task_id=task_id,state='submitted');save(record_path,record);event('submitted',task_id=task_id)
        task_id=record['task_id'];print('服务任务 ID：',task_id,flush=True)
        while time.monotonic()<deadline:
            d=request('GET','status',task_id,remaining=deadline-time.monotonic())
            status_value=field(d,CONFIG.get('response_fields',{}).get('status','status'))
            state=CONFIG.get('status_values',{}).get(status_value,status_value)
            if state not in ('queued','running','succeeded','failed','partial','cancelled'):raise ValueError('服务返回未知任务状态')
            record.update(state=state,last_status_time=time.time());save(record_path,record)
            event('status',task_id=task_id,state=state)
            print('服务状态：',state,flush=True)
            save(execution,dict(status='running',task_id=task_id,remote_status=state,elapsed_seconds=round(time.monotonic()-started,3)))
            if state in ('failed','partial','cancelled'):raise ValueError('服务任务 '+state+'，未形成完整答卷')
            if state=='succeeded':
                d=request('GET','result',task_id,remaining=deadline-time.monotonic())
                save(raw/(item.safe_id+'.result.json'),json.loads(redact(json.dumps(d,ensure_ascii=False))))
                answer=convert(item,d,task_id)
                if CONFIG['simulation']:
                    save(raw/(item.safe_id+'.simulation.json'),answer);status='simulation_complete';print('模拟流程完成；不生成正式答卷，不允许评分。')
                else:save(run/'results'/(item.safe_id+'.json'),answer);status='exited'
                return
            time.sleep(min(CONFIG.get('poll_seconds',3),max(0,deadline-time.monotonic())))
        status='remote_wait_timeout';raise ValueError('本地等待超时，服务端任务未确认停止；可恢复查询同一任务，不要重复创建')
    except Exception as exc:
        error=redact(str(exc))[:2000]
        event('error',error=error)
        raise
    finally:
        if status=='running':status='remote_needs_attention'
        save(execution,dict(status=status,error=error,task_id=(record or {}).get('task_id'),remote_status=(record or {}).get('state'),exit_code=0 if status in ('exited','simulation_complete') else None,elapsed_seconds=round(time.monotonic()-started,3),remote_stop_confirmed=False))
        event('local_wait_end',status=status)
        print('请求与状态已保存。停止本地等待不等于取消服务任务。',flush=True)


def _stage_review(items,run,preview=False):
    for item in items:run_case(item,run,preview)


def recover_answer(item,run):run_case(item,run,resume=True)


def write_summary(items,run):
    lines=['# 服务端 Agent 执行记录','','本地停止/超时不等于服务端已取消。恢复只查询原任务，不创建新任务。','']
    for item in items:
        p=run/'raw'/(item.safe_id+'.remote.json');d=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
        lines.append(f"- {item.instance_id}: {d.get('state','未提交')}；任务 ID：{d.get('task_id','未知')}")
    (run/'run-summary.zh-CN.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
