"""Comparison summaries retain missing data and use common scored cases for quality."""
import json
import math
from pathlib import Path
from runner.storage import save
from .scorer import counts


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def numeric(value):return type(value) in (int,float) and math.isfinite(value) and value>=0

def percentile(values,q):
    if not values:return None
    v=sorted(values);at=(len(v)-1)*q;lo=int(at);return v[lo]+(v[min(lo+1,len(v)-1)]-v[lo])*(at-lo)

def aggregate(scores):
    return counts(sum(s['tp'] for s in scores),sum(s['total_candidates'] for s in scores),sum(s['total_golden'] for s in scores)) if scores else None

def build(folder):
    manifest=read(folder/'experiment.json');groups=[];case_ids=manifest['case_ids']
    for group in manifest['groups']:
        run=folder.parent/group['key'];cases=[];judge_tokens=[];scores={}
        for case in case_ids:
            safe=case.replace('/','__');statusfile=run/'comparison-status'/f'{safe}.json'
            status=read(statusfile) if statusfile.exists() else {'status':'pending'}
            answerfile=run/'results'/f'{safe}.json';answer=read(answerfile) if answerfile.exists() else {}
            scorefile=run/'comparison-scores'/f'{safe}.json'
            if scorefile.exists():
                score=read(scorefile)
                if score.get('status')=='complete':scores[case]=score['metrics']
            summary=answer.get('review',{}).get('summary',{})
            cases.append(dict(id=case,status=status['status'],elapsed_seconds=status.get('review_seconds'),
                review_complete=answer.get('review',{}).get('status')=='complete',
                tokens={k:summary.get(k) for k in ('input_tokens','output_tokens','total_tokens')},metrics=scores.get(case),error=status.get('error')))
        for response in (run/'comparison-judge').glob('**/*.response.json'):
            judge_tokens.append(read(response).get('usage') or {})
        durations=[c['elapsed_seconds'] for c in cases if numeric(c['elapsed_seconds'])]
        complete=[c for c in cases if c['review_complete']]
        usage={k:sum(c['tokens'][k] for c in cases if numeric(c['tokens'][k])) for k in ('input_tokens','output_tokens','total_tokens')}
        coverage=sum(numeric(c['tokens']['total_tokens']) for c in cases)
        groups.append(dict(key=group['key'],label=group['label'],model=group['model'],cases=cases,
            completed=len(complete),scored=len(scores),failed=sum(c['status'] in ('review_failed','score_failed','setup_failed') for c in cases),
            timeouts=sum(c.get('error')=='TimeoutError' for c in cases),
            throughput_per_hour=3600*len(complete)/sum(durations) if durations and sum(durations)>0 else None,
            measured_review_seconds=sum(durations) if durations else None,
            mean_review_tokens={k:usage[k]/sum(numeric(c['tokens'][k]) for c in cases) if any(numeric(c['tokens'][k]) for c in cases) else None for k in usage},
            mean_seconds=sum(durations)/len(durations) if durations else None,p50=percentile(durations,.5),p90=percentile(durations,.9),
            review_tokens=usage,token_coverage=coverage,judge_tokens=sum(u.get('total_tokens') or 0 for u in judge_tokens),
            own_quality=aggregate(list(scores.values())),scores=scores))
    for g in groups:
        run=folder.parent/g['key']
        if g['scored']==len(case_ids):save(run/'metrics/one-to-one.json',dict(status='complete',summary=g['own_quality'],protocol=manifest['protocol']))
    common=[case for case in case_ids if all(case in g['scores'] for g in groups)]
    for g in groups:
        g['common_quality']=aggregate([g['scores'][c] for c in common]);
        g['case_macro']={k:sum(g['scores'][c][k] for c in common)/len(common) for k in ('precision','recall','f1')} if common else None
        del g['scores']
    baseline=groups[0]['common_quality'] if groups else None
    for g in groups:
        g['delta_from_first']={k:g['common_quality'][k]-baseline[k] for k in ('precision','recall','f1')} if baseline and g['common_quality'] else None
    result=dict(protocol=manifest['protocol'],suite=manifest['suite'],case_count=len(case_ids),common_cases=common,groups=groups)
    save(folder/'comparison.json',result)
    lines=['# 模型对比实验报告','',f"实验：{manifest['name']}；案例集：{manifest['suite']}；固定裁判：{manifest['judge_model']}",'',
           f"案例 {len(case_ids)} 个；各组共同完成评分 {len(common)} 个。质量主表仅比较共同案例，不把失败记为零分。",'',
           '| 模型配置 | 检视完成 | 评分完成 | 失败 | 检视平均秒 | P50 / P90 秒 | 检视 Token（已知） | 裁判 Token（已知） | 共同集 P / R / F1 |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---|']
    def fmt(v):return f'{v:.3f}' if v is not None else '—'
    def clean(s):return str(s).replace('|',' / ').replace('\n',' ')
    for g in groups:
        quality=g['common_quality'];prf=' / '.join(fmt(quality[k]) for k in ('precision','recall','f1')) if quality else '尚无共同有效评分'
        lines.append(f"| {clean(g['label'])} ({clean(g['model'])}) | {g['completed']}/{len(case_ids)} | {g['scored']}/{len(case_ids)} | {g['failed']} | {fmt(g['mean_seconds'])} | {fmt(g['p50'])} / {fmt(g['p90'])} | {g['review_tokens']['total_tokens']}（覆盖 {g['token_coverage']}/{len(case_ids)}） | {g['judge_tokens']} | {prf} |")
    lines+=['','计数口径：Martian 意见对语义判断 + 最大基数一对一匹配。FP 表示未匹配参考，并不证明意见事实错误。空答卷为有效零意见；缺失和失败无质量分。',
            '耗时为每案例检视命令端到端时间，包含启动开销及有记录的失败尝试；不含裁判，不代表首 Token 延迟。均值和分位数均基于已记录的尝试，分位数使用线性插值。吞吐不含准备、裁判和空闲时间，不等于整场实验吞吐。',
            'Token 为已有完整答卷和裁判响应中可采集的累计用量；失败响应可能缺失，已知值不是完整账单。此版本不自动重试。',
            '本实验是内部协议，不对比官方榜单；不合并不同案例集分数。当前被测工具为 LCR，尚未代表公司 Agent 对照完成。','']
    for g in groups:
        lines += [f"## {clean(g['label'])}",'',
                  f"输入 / 输出 / 总 Token（已知）：{g['review_tokens']['input_tokens']} / {g['review_tokens']['output_tokens']} / {g['review_tokens']['total_tokens']}；超时 {g['timeouts']} 例；有效完成 / 检视总用时吞吐：{fmt(g['throughput_per_hour'])} 例/小时。",
                  '每例平均输入 / 输出 / 总 Token（仅有用量记录的案例）：'+' / '.join(fmt(g['mean_review_tokens'][k]) for k in ('input_tokens','output_tokens','total_tokens')),
                  '案例宏平均 P/R/F1：'+ (' / '.join(fmt(g['case_macro'][k]) for k in ('precision','recall','f1')) if g['case_macro'] else '—'),
                  '相对首组的问题级 P/R/F1 差值：'+ (' / '.join(fmt(g['delta_from_first'][k]) for k in ('precision','recall','f1')) if g['delta_from_first'] else '—'),
                  '', '| 案例 | 执行状态 | 检视秒 | P / R / F1 |','|---|---|---:|---|']
        for c in g['cases']:
            m=c['metrics'];label={'pending':'待运行','reviewing':'检视中','scoring':'评分中','complete':'完成','review_failed':'检视失败','score_failed':'裁判失败','setup_failed':'准备失败'}.get(c['status'],c['status']);lines.append(f"| {clean(c['id'])} | {label} | {fmt(c['elapsed_seconds'])} | "+(' / '.join(fmt(m[k]) for k in ('precision','recall','f1')) if m else '—')+' |')
    (folder/'report.zh-CN.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return result
