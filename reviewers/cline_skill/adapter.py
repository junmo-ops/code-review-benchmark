"""Pinned Cline + Skill pilot. macOS snapshot isolation, shared benchmark answer format."""
import hashlib,json,os,platform,shutil,subprocess,tarfile,tempfile
from pathlib import Path
from runner.storage import ROOT,save,validate_answer
from runner.process import capture_process

CAPABILITIES=frozenset({'git_range','findings'})
VERSION='3.0.68'
RUNTIME=ROOT/'runtimes/cline'
BINARY=RUNTIME/'node_modules/.bin/cline'
SKILL=Path(__file__).parent/'skills/benchmark-review/SKILL.md'
REPO=None
CFG={}
PROMPT='Use the benchmark-review skill to review the change described by REVIEW_REQUEST.json and REVIEW_DIFF.patch. First load that skill, then inspect relevant repository code. Do not modify code. Return the final JSON answer required by the skill and finish.'

def tool_identity():
    if not BINARY.exists():raise ValueError('请先安装项目固定版本 Cline，见 docs/Cline试跑.md')
    return dict(version=VERSION,adapter_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),skill_sha256=hashlib.sha256(SKILL.read_bytes()).hexdigest(),package_lock_sha256=hashlib.sha256((RUNTIME/'package-lock.json').read_bytes()).hexdigest())

def configure(cfg,run):
    global CFG
    CFG=cfg

def redact(text):
    for name in ('LCR_LLM_TOKEN','JUDGE_API_KEY'):
        value=os.getenv(name)
        if value:text=str(text).replace(value,'[REDACTED]')
    return str(text)

def require_model(cfg):
    if os.getenv('LCR_LLM_MODEL')!=cfg['review_model'] or not os.getenv('LCR_LLM_TOKEN'):raise ValueError('模型配置与冻结批次不一致或缺少凭证')

def git(*args):return subprocess.check_output(['git','-C',str(REPO),*args],timeout=60)

def check_repos(items):
    if platform.system()!='Darwin':raise ValueError('Cline 首轮适配只验证 macOS；Linux 需提供对应隔离环境后启用')
    version=subprocess.check_output([str(BINARY),'--version'],text=True,timeout=20).strip()
    if version!=VERSION:raise ValueError('Cline 版本与适配版本不符')
    for item in items:
        git('cat-file','-e',item.head_commit+'^{commit}')
        git('merge-base',item.base_commit,item.head_commit)

def process_timeout(item):return CFG['timeout_minutes']*60+20

def convert(item,rows):
    finals=[d for d in rows if d.get('type')=='run_result']
    if len(finals)!=1 or finals[0].get('finishReason')!='completed':raise ValueError('Cline 未确认完整结束')
    loaded=[d for d in rows if d.get('event',{}).get('type')=='content_end' and d['event'].get('contentType')=='tool' and d['event'].get('toolName')=='skills' and '<command-name>benchmark-review</command-name>' in str(d['event'].get('output',''))]
    if not loaded:raise ValueError('未找到检视 Skill 实际加载证据')
    final=finals[0];answer=json.loads(final.get('text',''))
    if not isinstance(answer,dict) or answer.get('status')!='complete' or not isinstance(answer.get('comments'),list):raise ValueError('Cline 答卷格式或完整性无效')
    for c in answer['comments']:
        if not isinstance(c,dict):raise ValueError('意见必须为对象')
        p=c.get('path');start=c.get('start_line');end=c.get('end_line');content=c.get('content')
        if not isinstance(p,str) or not p or p.startswith('/') or '\\' in p or ':' in p or '..' in Path(p).parts or type(start) is not int or type(end) is not int or start<1 or end<start or not isinstance(content,str) or not content.strip():raise ValueError('意见位置或内容无效')
    usage=final.get('aggregateUsage') or final.get('usage') or {};summary={}
    for source,target in [('inputTokens','input_tokens'),('outputTokens','output_tokens'),('cacheReadTokens','cache_read_tokens')]:
        value=usage.get(source)
        if value is not None:
            if type(value) is not int or value<0:raise ValueError('Token 用量无效')
            summary[target]=value
    if 'input_tokens' in summary and 'output_tokens' in summary:summary['total_tokens']=summary['input_tokens']+summary['output_tokens']
    return dict(instance_id=item.instance_id,repo=item.repo,base_commit=item.base_commit,head_commit=item.head_commit,reviewer='cline_skill',review=dict(status='complete',comments=answer['comments'],summary=summary,llm={'model':CFG['review_model']}),skill=dict(name='benchmark-review',sha256=hashlib.sha256(SKILL.read_bytes()).hexdigest(),loaded=True),runtime=dict(version=VERSION,iterations=final.get('iterations'),reported_duration_ms=final.get('durationMs'),cost='not_verified'))

def recover_answer(item,run):
    out=run/'results'/(item.safe_id+'.json')
    if out.exists():validate_answer(item,out);return
    execution=json.loads((run/'raw'/(item.safe_id+'.execution.json')).read_text())
    manifest=json.loads((run/'pilot-manifest.json').read_text())
    if execution.get('status')!='exited' or execution.get('exit_code')!=0:raise ValueError('原执行没有正常完成，禁止作为完整答卷')
    if (manifest['instance_id'],manifest['base'],manifest['head'],manifest['model'],manifest['skill_sha256'],manifest['cline_version'])!=(item.instance_id,item.base_commit,item.head_commit,CFG['review_model'],hashlib.sha256(SKILL.read_bytes()).hexdigest(),VERSION):raise ValueError('执行清单与批次不一致')
    raw=run/'raw'/(item.safe_id+'.stdout.jsonl')
    rows=[json.loads(l) for l in raw.read_text().splitlines() if l.strip()]
    answer=convert(item,rows);answer['duration_seconds']=execution['elapsed_seconds'];answer['raw_sha256']=hashlib.sha256(raw.read_bytes()).hexdigest()
    save(out,answer)
    print('Cline 完整答卷已保存；Skill 加载已验证；意见数：',len(answer['review']['comments']))

def _stage_review(items,run,preview=False):
    for item in items:
        if preview:
            print(git('diff','--stat',git('merge-base',item.base_commit,item.head_commit).decode().strip(),item.head_commit).decode());continue
        if (run/'raw'/(item.safe_id+'.execution.json')).exists():raise ValueError('已有执行记录，请先恢复或检查；不自动重新调用模型')
        run_case(item,run)

def run_case(item,run):
    work=Path(tempfile.mkdtemp(prefix='cline-review-')).resolve();w=work/'repo';cfg=work/'config';tmp=work/'temp';cfg.mkdir();tmp.mkdir()
    try:
        with tempfile.TemporaryFile() as archive:
            subprocess.run(['git','-C',str(REPO),'archive',item.head_commit],stdout=archive,check=True);archive.seek(0)
            with tarfile.open(fileobj=archive) as t:t.extractall(w,filter='data')
        skill_dest=w/'.cline/skills/benchmark-review'
        if skill_dest.exists():raise ValueError('案例仓库存在同名 Skill，需明确处理后再运行')
        skill_dest.mkdir(parents=True);shutil.copy2(SKILL,skill_dest/'SKILL.md')
        ancestor=git('merge-base',item.base_commit,item.head_commit).decode().strip()
        (w/'REVIEW_DIFF.patch').write_bytes(git('diff',ancestor,item.head_commit,'--'))
        save(w/'REVIEW_REQUEST.json',dict(repository=item.repo,base_commit=item.base_commit,head_commit=item.head_commit,diff_mode='merge_base',merge_base=ancestor))
        provider=dict(version=1,lastUsedProvider='openai-compatible',modes={},providers={'openai-compatible':dict(settings=dict(provider='openai-compatible',apiKey=os.environ['LCR_LLM_TOKEN'],model=CFG['review_model'],baseUrl=os.environ['LCR_LLM_BASE_URL']),tokenSource='manual')})
        f=cfg/'data/settings/providers.json';save(f,provider);f.chmod(0o600)
        q=lambda p:json.dumps(str(p.resolve()))
        policy='(version 1)\n(allow default)\n(deny file-read* (subpath "/Users"))\n(allow file-read* (subpath %s) (subpath %s) (subpath %s))\n(deny file-write*)\n(allow file-write* (subpath %s) (subpath %s) (subpath "/dev"))\n'%(q(w),q(cfg),q(RUNTIME),q(cfg),q(tmp))
        (work/'sandbox.sb').write_text(policy)
        budget=CFG['timeout_minutes']*60
        save(run/'pilot-manifest.json',dict(instance_id=item.instance_id,base=item.base_commit,head=item.head_commit,model=CFG['review_model'],cline_version=VERSION,skill_sha256=hashlib.sha256(SKILL.read_bytes()).hexdigest(),budget_seconds=budget,simulation=False,context='head snapshot plus merge-base diff',sandbox='macOS read-only case snapshot; benchmark directory denied'))
        command=['/usr/bin/sandbox-exec','-f',str(work/'sandbox.sb'),str(BINARY.resolve()),'--config',str(cfg),'--data-dir',str(cfg/'data'),'--cwd',str(w),'--provider','openai-compatible','--model',CFG['review_model'],'--json','--timeout',str(budget),'--retries','1',PROMPT]
        env_updates=dict(HOME=str(cfg),TMPDIR=str(tmp),XDG_CACHE_HOME=str(cfg/'cache'),XDG_CONFIG_HOME=str(cfg))
        old={k:os.environ.get(k) for k in env_updates}
        try:
            os.environ.update(env_updates)
            completed=capture_process(command,run/'raw',item.safe_id,timeout=budget+20,cwd=w,stdout_suffix='.stdout.jsonl',redact=redact)
        finally:
            for k,v in old.items():
                if v is None:os.environ.pop(k,None)
                else:os.environ[k]=v
        if completed.returncode:raise ValueError('Cline 执行失败，详见原始日志')
        recover_answer(item,run)
    finally:
        shutil.rmtree(work)

def write_summary(items,run):
    lines=['# Cline + Skill 执行记录','','固定 Cline '+VERSION+'，benchmark-review Skill；真实调用模型。','']
    for item in items:
        p=run/'results'/(item.safe_id+'.json')
        if p.exists():
            a=validate_answer(item,p);lines.append(f"- {item.instance_id}：完成，{len(a['review']['comments'])} 条意见，{a.get('duration_seconds')} 秒；Skill 已加载。")
        else:lines.append(f'- {item.instance_id}：无完整答卷，请查看 raw 日志。')
    (run/'run-summary.zh-CN.md').write_text('\n'.join(lines)+'\n')
