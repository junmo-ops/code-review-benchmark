"""Read-only experiment presentation and user labels; no scoring or CLI changes."""
import json
from .service import read, save


def title(value):
    if value is None:return ''
    if not isinstance(value,str) or len(value)>80 or any(ord(c)<32 for c in value):
        raise ValueError('显示名称须为不超过 80 字符的单行文字')
    return value.strip()


def submit_named(app,data,comparison=False):
    if not isinstance(data,dict):raise ValueError('请求必须是对象')
    label=title(data.get('display_name'))
    if comparison:
        from comparison.service import create
        result=create(app,data)
    else:
        from runner.diagnostics import phase
        with phase('benchmark.submit'):
            result=app.submit(data)
    if label and (comparison or data.get('action')=='create'):
        with app.lock:
            path=app.state/'display-names.json'
            labels=read(path) if path.exists() else {}
            labels[result['key']]=label;save(path,labels)
    return result


def decorate(app,experiments,runs):
    path=app.state/'display-names.json';names=read(path) if path.exists() else {}
    for item in runs+experiments:item['display_name']=names.get(item['key'],item['name'])
    for item in experiments:
        m=safe_read(app.run_dir(item['key']), 'experiment.json')
        item['budget']=dict(review_seconds=m.get('case_timeout_seconds'),judge_seconds=m.get('judge_case_timeout_seconds'),protocol=m.get('protocol'))
    return experiments


def safe_read(root,relative,default=None):
    path=root/relative
    if not path.resolve().is_relative_to(root.resolve()) or path.resolve()!=path.absolute():raise ValueError('证据路径不可用')
    return read(path) if path.is_file() else ({} if default is None else default)


def case_evidence(app,key,group,case_id):
    root=app.run_dir(key);manifest=safe_read(root,'experiment.json')
    if group not in {g['key'] for g in manifest.get('groups',[])} or case_id not in manifest.get('case_ids',[]):
        raise ValueError('案例不属于此实验分组')
    run=app.run_dir(group)
    from suites.registry import get_suite
    from evaluators.martian import golden_rows
    suite_id=manifest['suite'];suite=get_suite(suite_id)
    dataset=root/'dataset.jsonl'
    if dataset.resolve()!=dataset.absolute():raise ValueError('案例清单不可用')
    cases=suite.load_cases(dataset)
    case=next(c for c in cases if c.instance_id==case_id)
    rows=[json.loads(line) for line in dataset.read_text(encoding='utf-8').splitlines() if line.strip()]
    row=rows[cases.index(case)]
    references=suite.reference_comments(row) if suite_id=='codefuse_cr' else golden_rows(row,suite_id)
    safe=case.safe_id
    score=safe_read(run,f'comparison-scores/{safe}.json')
    answer=safe_read(run,f'results/{safe}.json')
    status=safe_read(run,f'comparison-status/{safe}.json',{'status':'pending'})
    execution=safe_read(run,f'raw/{safe}.execution.json')
    # Diagnostic PID comes from the persisted execution record, never from an arbitrary path.
    diagnostic=dict(http_statuses={},retries=0,deadline_calls=[],pending_calls=[],ended_calls=0)
    pid=execution.get('pid');starts={};ends=set();deadlines=set();paths=[]
    if type(pid) is int:
        for p in sorted((run/'diagnostics').glob(f'diagnostics-*-{pid}.jsonl')):
            if p.resolve()!=p.absolute():continue
            paths.append(str(p.relative_to(run)))
            for line in p.read_text(encoding='utf-8').splitlines():
                try:x=json.loads(line)
                except ValueError:continue
                event=x.get('event');cid=x.get('call_id')
                if event=='llm_call_start':starts[cid]=dict(call_id=cid,file=x.get('file'),task=x.get('task'))
                if event=='http_attempt_end' and x.get('deadline_exceeded'):deadlines.add(cid)
                if event=='llm_call_end':
                    ends.add(cid)
                    if cid in deadlines:diagnostic['deadline_calls'].append(dict(**starts.get(cid,{'call_id':cid}),seconds=x.get('duration_ms',0)/1000))
                if event=='http_attempt_headers':
                    code=str(x.get('http_status'));diagnostic['http_statuses'][code]=diagnostic['http_statuses'].get(code,0)+1
                if event=='http_attempt_start' and x.get('attempt',1)>1:diagnostic['retries']+=1
        diagnostic['ended_calls']=len(ends);diagnostic['pending_calls']=[v for k,v in starts.items() if k not in ends]
    artifacts={x['path'] for x in app.artifacts(group)}
    evidence_paths=[f'comparison-status/{safe}.json',f'comparison-scores/{safe}.json',f'results/{safe}.json',f'raw/{safe}.stderr.txt',f'raw/{safe}.execution.json',*paths]
    value=dict(case_id=case_id,group=group,status=status,references=references,
               comments=answer.get('review',{}).get('comments',[]),score=score,
               diagnostic=diagnostic if paths else None,artifacts=[p for p in evidence_paths if p in artifacts])
    return json.loads(app.redact(json.dumps(value,ensure_ascii=False)))
