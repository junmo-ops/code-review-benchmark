from runner.runtime import data_root, config_root, docker_context
import argparse,asyncio,hashlib,json,os,shutil,subprocess,sys,tarfile,time
from pathlib import Path
from runner.storage import ROOT,save
from runner.cloud import exclusive
from runner.process import capture_process
from .core import load,read,sha,identity,validate_card,matching_pairs,metrics,upstream,calculate_metrics
from .api import call

def endpoint(key):return hashlib.sha256(os.environ.get(key,'').encode()).hexdigest()
def rows(cases):return [(c,r) for c in cases for r in c['rounds']]
def complete(run,row,reviewer):
    p=run/'rounds'/row['task_id']/'answer.json'
    if not p.exists():return False
    validate_card(read(p),row,reviewer);return True

def verify(cfg,run):
    if sha(run/'dataset.jsonl')!=cfg['dataset_sha256'] or identity()!=cfg['identity']:raise ValueError('冻结数据或多轮代码已变化，请新建批次')

def check_model(cfg,role):
    keys=('LCR_LLM_MODEL','LCR_LLM_BASE_URL','LCR_LLM_TOKEN') if role=='review' else ('JUDGE_MODEL','JUDGE_BASE_URL','JUDGE_API_KEY')
    if any(not os.getenv(k) for k in keys):raise ValueError('缺少 '+role+' 模型配置')
    if os.environ[keys[0]]!=cfg[role+'_model'] or endpoint(keys[1])!=cfg[role+'_endpoint_sha256']:raise ValueError('模型或接口与冻结配置不同')
    if role=='judge' and os.getenv('JUDGE_USE_MOCK','false').lower() not in ('false','0','no',''):raise ValueError('禁止 Mock 评分')

def start_attempt(folder,retry):
    if folder.exists():
        if not retry:raise ValueError('已有未完成尝试，请检查日志；确认旧任务停止后使用 --retry')
        dest=folder.parent/'attempts'/f'{folder.name}-{time.time_ns()}'
        dest.parent.mkdir(exist_ok=True);folder.rename(dest)

def init(args,run):
    if (run/'run-config.json').exists():raise ValueError('批次已存在')
    if not args.dataset:raise ValueError('init 需要 --dataset')
    if args.max_output_tokens<1:raise ValueError('输出上限必须为正整数')
    cases=load(args.dataset)
    provenance=ROOT/'data/sources/mcr/provenance.json';manifest=read(provenance)
    digest=sha(args.dataset)
    if digest==manifest['dataset_sha256']:
        for name,value in manifest['files'].items():
            if sha(provenance.parent/name)!=value:raise ValueError('原始数据来源哈希变化')
        source={'status':'pinned_official_source','manifest':manifest}
    else:source={'status':'unverified_custom_dataset'}
    cfg={'protocol':'mcr_published_prior_history_replay_v1','reviewer':args.reviewer,'identity':identity(),
         'dataset_sha256':digest,'review_model':os.getenv('LCR_LLM_MODEL'),'judge_model':os.getenv('JUDGE_MODEL'),
         'review_endpoint_sha256':endpoint('LCR_LLM_BASE_URL'),'judge_endpoint_sha256':endpoint('JUDGE_BASE_URL'),
         'parameters':{'temperature':0,'max_tokens':args.max_output_tokens,'request_timeout_seconds':180,'max_retries':0},
         'provenance':source,'official_leaderboard_comparable':False,'state_metrics':'internal_matched_state_v1'}
    if args.reviewer=='lcr':
        from reviewers.lcr.adapter import tool_identity
        cfg['lcr_identity']=tool_identity()
        from reviewers.lcr.options import freeze_options
        cfg['lcr_options']=freeze_options()
    (run/'dataset.jsonl').write_bytes(Path(args.dataset).read_bytes());save(run/'run-config.json',cfg)
    for case,row in rows(cases):
        folder=run/'rounds'/row['task_id'];folder.mkdir(parents=True)
        # Only explicit allowlisted model input; annotations stay outside this file.
        save(folder/'input.json',{'task_id':row['task_id'],'round_id':row['round_id'],'messages':[{'role':'user','content':row['prompt']}]})
    print('已冻结 MCR 批次：',run,'；PR 数：',len(cases),'；轮数：',len(rows(cases)),flush=True)

def prepare_repos(cases):
    from tools.repo_snapshot import restore_snapshot
    for case in cases:
        slug=case['repo'].replace('/','__');repo=data_root(ROOT)/'repos'/slug
        with exclusive(data_root(ROOT)/'repos'/'.mcr-locks'/slug):
            if not repo.exists():
                restore_snapshot(ROOT/'bundles'/(slug+'.git.tar.gz'),repo)
                subprocess.run(['git','-C',str(repo),'checkout','--force','--detach',case['rounds'][0]['code_diff']['commits'][-1]['sha']],check=True,capture_output=True)
            for row in case['rounds']:
                for c in row['code_diff']['commits']:
                    subprocess.run(['git','-C',str(repo),'cat-file','-e',c['sha']+'^{commit}'],check=True,capture_output=True)
                subprocess.run(['git','-C',str(repo),'rev-parse','--verify',row['code_diff']['commits'][0]['sha']+'^'],check=True,capture_output=True)
        print('离线仓库就绪：',repo,flush=True)

def selected(cases,task):
    values=rows(cases)
    if task:
        values=[v for v in values if v[1]['task_id']==task]
        if not values:raise ValueError('轮次不属于本批次')
    return values

def lcr_review(case,row,folder,cfg):
    with exclusive(data_root(ROOT)/'repos'/'.mcr-locks'/case['repo'].replace('/','__')):
        return lcr_review_locked(case,row,folder,cfg)

def lcr_review_locked(case,row,folder,cfg):
    from reviewers.lcr.adapter import tool_identity, convert
    if cfg['lcr_identity']!=tool_identity():raise ValueError('LCR 版本或规则变化')
    repo=data_root(ROOT)/'repos'/case['repo'].replace('/','__')
    commits=row['code_diff']['commits'];head=commits[-1]['sha']
    base=subprocess.check_output(['git','-C',str(repo),'rev-parse',commits[0]['sha']+'^'],text=True).strip()
    if subprocess.check_output(['git','-C',str(repo),'status','--porcelain'],text=True).strip():raise ValueError('MCR 仓库有本地改动，停止切换提交')
    subprocess.run(['git','-C',str(repo),'checkout','--detach',head],check=True,capture_output=True)
    actual=subprocess.check_output(['git','-C',str(repo),'diff','--no-ext-diff','--no-textconv',base,head],text=True)
    def canonical(s):
        import re
        return '\n'.join(re.sub(r'^(@@ .*? @@).*$',r'\1',x) for x in s.split('\n') if not x.startswith(('index ','--- ','+++ '))).rstrip('\n')
    if canonical(actual)!=canonical(row['code_diff']['diff']):raise ValueError('LCR 实际 diff 与当轮官方 patch 不一致')
    prompt=row['prompt'];background=prompt.split('**Input Data:**',1)[1].split('**Output:**',1)[0]
    path=folder/'background.md';path.write_text(background,encoding='utf-8')
    save(folder/'range.json',{'base':base,'head':head,'background_sha256':sha(path)})
    command=[str(ROOT/'bin/lcr'),'review','--repo',str(repo),'--from',base,'--to',head,'--background-file',str(path),
             '--format','json','--audience','human','--concurrency','1','--timeout','10','--keep-session']
    from reviewers.lcr.options import freeze_options, command_options
    options = cfg.get('lcr_options') or freeze_options()
    command.extend(command_options(options))
    os.environ["LCR_LLM_STREAM"] = str(options["stream"]).lower()
    def redact(text):
        for key in ('LCR_LLM_TOKEN','JUDGE_API_KEY'):
            if os.getenv(key):text=text.replace(os.environ[key],'[REDACTED]')
        return text
    result=capture_process(command,folder,'lcr',timeout=options.get('case_timeout_seconds') or 720,cwd=repo,redact=redact)
    if result.returncode:raise ValueError('LCR 失败，参见当轮日志')
    raw=json.loads(result.stdout)
    if raw.get('status') == 'budget_exceeded':
        raise ValueError('Token 预算耗尽；部分结果已保留，不自动重试')
    if raw.get('status') not in ('success','completed') or not isinstance(raw.get('comments'),list):raise ValueError('LCR 未返回完整答卷')
    from types import SimpleNamespace
    convert(SimpleNamespace(instance_id=row['task_id'],repo=case['repo'],base_commit=base,head_commit=head),raw)
    findings=[]
    for i,c in enumerate(raw['comments']):
        findings.append({'defect_id':f'DEFECT-{i+1}','title':c.get('category','LCR finding'),'description':c['description'],
                         'location':{'file':c.get('filePath'),'lines':str(c.get('startLine','unknown'))},
                         'state_transition':None,'state_support':'unsupported_native_output'})
    return {'task_id':row['task_id'],'round_id':row['round_id'],'defects':findings,'reviewer':'lcr','state_support':'unsupported_native_output'}

def review(cases,cfg,run,args):
    check_model(cfg,'review')
    for case,row in selected(cases,args.task):
        if complete(run,row,cfg['reviewer']):print('跳过完整轮次：',row['task_id'],flush=True);continue
        for previous in case['rounds'][:row['round_id']-1]:
            if not complete(run,previous,cfg['reviewer']):raise ValueError('请先完成前面的轮次')
        folder=run/'rounds'/row['task_id'];attempt=folder/'review';start_attempt(attempt,args.retry)
        print('检视轮次：',row['task_id'], '被测对象：',cfg['reviewer'],flush=True)
        try:
            if cfg['reviewer']=='llm_direct':
                expected={'task_id':row['task_id'],'round_id':row['round_id'],'messages':[{'role':'user','content':row['prompt']}]}
                if read(folder/'input.json')!=expected:raise ValueError('检视输入与冻结数据不一致')
                answer=call(expected['messages'],'review',cfg,attempt)
            else:
                attempt.mkdir();answer=lcr_review(case,row,attempt,cfg)
            validate_card(answer,row,cfg['reviewer']);save(folder/'answer.json',answer)
            save(folder/'review-status.json',{'status':'complete'})
        except BaseException as exc:
            save(folder/'review-status.json',{'status':'failed','error_type':type(exc).__name__});raise
        print('轮次完成：',row['task_id'],flush=True)

def evaluate(cases,cfg,run,args):
    check_model(cfg,'judge')
    for case,row in selected(cases,args.task):
        folder=run/'rounds'/row['task_id']
        if not complete(run,row,cfg['reviewer']):raise ValueError('评分需要完整轮次答卷')
        output=folder/'score.json'
        if output.exists():print('跳过已有评分：',row['task_id'],flush=True);continue
        refs=row['ground_truth']['defects'];preds=read(folder/'answer.json')['defects']
        attempt=folder/'judge';start_attempt(attempt,args.retry)
        try:
            if refs and preds:
                system,user=upstream.build_matching_prompt(refs,preds,row['task_id'])
                decision=call([{'role':'system','content':system},{'role':'user','content':user}],'judge',cfg,attempt)
                pairs=matching_pairs(decision,refs,preds)
            else:
                attempt.mkdir();save(attempt/'decision.json',{'no_api_call':'empty_side'});pairs=[]
            value=metrics(refs,preds,pairs)
            save(output,{'status':'complete','task_id':row['task_id'],'metrics':value,'pairs':pairs,
                         'answer_sha256':sha(folder/'answer.json'),'state_protocol':'internal_matched_state_v1'})
        except BaseException as exc:
            save(folder/'judge-status.json',{'status':'failed','error_type':type(exc).__name__});raise
        print('评分完成：',row['task_id'],value,flush=True)
    report(cases,cfg,run)

def report(cases,cfg,run):
    scores=[];states=[]
    for case,row in rows(cases):
        folder=run/'rounds'/row['task_id'];done=complete(run,row,cfg['reviewer']);score=None
        if (folder/'score.json').exists():
            score=read(folder/'score.json')
            if score['answer_sha256']!=sha(folder/'answer.json'):raise ValueError('答卷在评分后变化')
            scores.append(score)
        states.append({'task_id':row['task_id'],'review_complete':done,'score_complete':score is not None,'score':score})
    n=len(states);full=len(scores)==n
    summary={'status':'complete' if full else 'partial','reviewer':cfg['reviewer'],'total_rounds':n,
             'reviewed_rounds':sum(x['review_complete'] for x in states),'scored_rounds':len(scores),
             'complete_prs':sum(all(next(x for x in states if x['task_id']==rr['task_id'])['score_complete'] for rr in c['rounds']) for c in cases),'total_prs':len(cases)}
    if full:
        total={k:sum(s['metrics'][k] for s in scores) for k in ('tp','fp','fn','state_known','state_correct','transition_correct')}
        summary.update(total);summary.update(calculate_metrics(total['tp'],total['fp'],total['fn']))
        summary['state_accuracy']=total['state_correct']/total['state_known'] if total['state_known'] else None
        summary['state_coverage']=total['state_known']/total['tp'] if total['tp'] else None
        summary['transition_accuracy']=total['transition_correct']/total['state_known'] if total['state_known'] else None
    save(run/'metrics.json',{'summary':summary,'rounds':states})
    lines=['# MCR 多轮专项报告','',f"批次：`{run.name}`；被测对象：`{cfg['reviewer']}`；检视模型：`{cfg['review_model']}`；裁判：`{cfg['judge_model']}`。",'',
           f"状态：{summary['status']}；检视完成 {summary['reviewed_rounds']}/{n} 轮；评分完成 {len(scores)}/{n} 轮；完整 PR {summary['complete_prs']}/{len(cases)}。",'',
           '输入采用固定版本的公开逐轮提示词：当轮 diff + 本轮之前的历史讨论，不追加被测模型前轮答卷，不是自主修改闭环。参考标注只进入裁判。',
           '复用上游一对一匹配提示词及 P/R/F1 函数；本报告的 micro 汇总和状态指标是明确标注的内部协议，不声称复现官方榜单。未匹配不等于已证实误报。',
           '状态准确率仅在已匹配且明确输出状态的问题上计算，须与缺陷召回和状态覆盖率一起看。未输出状态不能推断为已解决；LCR 原生缺少状态字段，标记 unsupported，不由裁判补做。','',
           '| 轮次 | 检视完成 | 评分完成 | F1 | 状态正确/有状态匹配 |','|---|---|---|---:|---:|']
    for item in states:
        m=item['score']['metrics'] if item['score'] else {}
        lines.append(f"| {item['task_id']} | {item['review_complete']} | {item['score_complete']} | {m.get('f1','—')} | {str(m.get('state_correct','—'))+'/'+str(m.get('state_known','—'))} |")
    if full:
        lines += ['',f"整体缺陷 P/R/F1：{summary['precision']:.3f} / {summary['recall']:.3f} / {summary['f1']:.3f}。",
                  f"状态覆盖率：{summary['state_coverage']}；状态准确率：{summary['state_accuracy']}；完整转移准确率：{summary['transition_accuracy']}。"]
    else:lines+=['','尚未全部评分，未发布完整 PR 汇总分数。']
    if any((run/'rounds'/r['task_id']/'martian/score.json').exists() for _,r in rows(cases)):
        lines += ['','Martian 缺陷匹配对照结果保存在各轮 `martian/score.json`；其原生匹配计数与 MCR 一对一协议不同，不评价状态，不合并分数。']
    (run/'report.zh-CN.md').write_text('\n'.join(lines)+'\n',encoding='utf-8');print('报告：',run/'report.zh-CN.md',flush=True)

async def martian_compare(cases,cfg,run,args):
    from openai import AsyncOpenAI
    from evaluators.martian import Judge,upstream as native
    from evaluators.registry import PARAMETERS
    check_model(cfg,'judge')
    for case,row in selected(cases,args.task):
        if not complete(run,row,cfg['reviewer']):raise ValueError('需要完整答卷')
        folder=run/'rounds'/row['task_id']/'martian'
        if (folder/'score.json').exists():
            if read(folder/'score.json')['answer_sha256']!=sha(run/'rounds'/row['task_id']/'answer.json'):raise ValueError('Martian 评分后的答卷已变化')
            continue
        start_attempt(folder,args.retry)
        refs=[{'comment':d['title']+'\n'+d['description']} for d in row['ground_truth']['defects']]
        preds=[d['title']+'\n'+d['description'] for d in read(run/'rounds'/row['task_id']/'answer.json')['defects']]
        async with AsyncOpenAI(api_key=os.environ['JUDGE_API_KEY'],base_url=os.environ['JUDGE_BASE_URL'],timeout=180,max_retries=0) as client:
            result=await native.evaluate_review(Judge(client,cfg['judge_model'],folder,PARAMETERS),refs,preds)
        save(folder/'upstream.json',result)
        if result.get('errors_count') or result.get('skipped'):raise ValueError('Martian 部分失败，不计分')
        save(folder/'score.json',{'status':'complete','answer_sha256':sha(run/'rounds'/row['task_id']/'answer.json'),'metrics':result,'state_metrics':'not_supported','note':'上游可能多对一，不能当成 MCR 一对一分数；只作缺陷匹配对照'})
        print('Martian 对照完成：',row['task_id'],flush=True)

def main():
    p=argparse.ArgumentParser(description='MCR 独立多轮专项；不修改单轮流水线')
    p.add_argument('stage',choices=['init','review','eval','report','status','compare-martian','export','prepare-repos'])
    p.add_argument('--run-id',required=True);p.add_argument('--dataset',type=Path)
    p.add_argument('--reviewer',choices=['llm_direct','lcr'],default=None)
    p.add_argument('--max-output-tokens',type=int,default=32768,help='init 冻结直接模型与主裁判的输出上限')
    p.add_argument('--task',help='只执行指定轮次 task_id');p.add_argument('--retry',action='store_true')
    args=p.parse_args()
    if not re_safe(args.run_id):p.error('批次名无效')
    if args.stage!='init' and (args.reviewer or args.dataset):p.error('reviewer/dataset 仅 init 指定')
    run=data_root(ROOT)/'runs/mcr'/args.run_id
    with exclusive(run):
        if args.stage=='init':args.reviewer=args.reviewer or 'llm_direct';init(args,run);return
        cfg=read(run/'run-config.json');verify(cfg,run);cases=load(run/'dataset.jsonl')
        if args.stage=='prepare-repos':prepare_repos(cases)
        elif args.stage=='review':
            try:review(cases,cfg,run,args)
            finally:report(cases,cfg,run)
        elif args.stage=='eval':evaluate(cases,cfg,run,args)
        elif args.stage=='compare-martian':
            asyncio.run(martian_compare(cases,cfg,run,args));report(cases,cfg,run)
        elif args.stage in ('report','status'):report(cases,cfg,run)
        else:
            dest=run.parent/(args.run_id+'-results.tar.gz')
            with tarfile.open(dest,'w:gz') as tar:
                for path in sorted(run.rglob('*')):
                    if path.is_file() and not path.is_symlink() and path.name!='.cloud.lock':tar.add(path,arcname=str(Path(run.name)/path.relative_to(run)),recursive=False)
            print('结果归档：',dest)

def re_safe(value):
    import re
    return bool(re.fullmatch(r'[A-Za-z0-9_-]+',value))

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('已中断，按轮次保留结果。',file=sys.stderr);sys.exit(130)
    except Exception as exc:
        detail=str(exc) if isinstance(exc,ValueError) else '请检查当轮持久化日志'
        for key in ('LCR_LLM_TOKEN','JUDGE_API_KEY'):
            if os.getenv(key):detail=detail.replace(os.environ[key],'[REDACTED]')
        print('MCR 停止：'+type(exc).__name__+'；'+detail,file=sys.stderr);sys.exit(1)
