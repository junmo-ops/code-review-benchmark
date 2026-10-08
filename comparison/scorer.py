"""Martian semantic decisions with deterministic maximum-cardinality matching."""
import asyncio
import hashlib
import json
from pathlib import Path
from runner.storage import ROOT, save, validate_answer
from suites.registry import get_suite
from evaluators.martian import Judge, golden_rows

PROTOCOL = 'martian_one_to_one_v1'
PARAMETERS = dict(temperature=0, max_tokens=8192, timeout_seconds=180, max_retries=0,
                  rounds=1, matching='maximum_cardinality_stable_index', dedup='none', max_pairs=400)


def identity():
    paths = list((ROOT/'comparison').glob('*.py')) + [ROOT/'evaluators/martian.py', ROOT/'vendor/martian/offline/code_review_benchmark/step3_judge_comments.py']
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def count_matches(gold_count, candidate_count, edges):
    adjacency = {g:[] for g in range(gold_count)}
    for g,c in sorted(set(map(tuple,edges))):
        if not 0<=g<gold_count or not 0<=c<candidate_count:raise ValueError('匹配索引越界')
        adjacency[g].append(c)
    assigned={}
    def augment(g,seen):
        for c in adjacency[g]:
            if c in seen:continue
            seen.add(c)
            if c not in assigned or augment(assigned[c],seen):
                assigned[c]=g;return True
        return False
    for g in adjacency:augment(g,set())
    pairs=sorted((g,c) for c,g in assigned.items());tp=len(pairs)
    return dict(**counts(tp,candidate_count,gold_count),pairs=pairs)


def counts(tp,candidates,gold):
    p=tp/candidates if candidates else 0.;r=tp/gold if gold else 0.
    return dict(tp=tp,fp=candidates-tp,fn=gold-tp,total_candidates=candidates,total_golden=gold,
                precision=p,recall=r,f1=2*p*r/(p+r) if p+r else 0.)


async def score_case(run, suite_id, case_id, env):
    from openai import AsyncOpenAI
    suite=get_suite(suite_id);cases=suite.load_cases(run/'dataset.jsonl')
    case=next(c for c in cases if c.instance_id==case_id)
    output=run/'comparison-scores'/f'{case.safe_id}.json'
    if output.exists():raise ValueError('已有评分，不覆盖；请新建实验')
    answer=validate_answer(case,run/'results'/f'{case.safe_id}.json')
    rows=[json.loads(s) for s in (run/'dataset.jsonl').read_text().splitlines() if s.strip()]
    row=rows[cases.index(case)]
    gold=suite.reference_comments(row) if suite_id=='codefuse_cr' else golden_rows(row,suite_id)
    candidates=[c['content'] for c in answer['review']['comments']]
    if not gold or any(not isinstance(c,str) or not c.strip() for c in candidates):raise ValueError('意见或参考无效')
    if len(gold)*len(candidates)>PARAMETERS['max_pairs']:raise ValueError('超过单案例裁判配对预算，停止计分')
    if env.get('JUDGE_USE_MOCK','false').lower() not in ('false','0','no',''):raise ValueError('禁止 Mock')
    evidence=run/'comparison-judge'/case.safe_id
    if evidence.exists():raise ValueError('已有裁判尝试，不自动重试')
    evidence.mkdir(parents=True)
    decisions=[];edges=[]
    try:
        async with AsyncOpenAI(api_key=env['JUDGE_API_KEY'],base_url=env['JUDGE_BASE_URL'],max_retries=0,timeout=180) as client:
            judge=Judge(client,env['JUDGE_MODEL'],evidence,PARAMETERS)
            for gi,g in enumerate(gold):
                for ci,c in enumerate(candidates):
                    decision=await judge.match_comment(g['comment'],c)
                    decisions.append(dict(golden=gi,candidate=ci,**decision))
                    if decision['match']:edges.append((gi,ci))
        value=dict(status='complete',instance_id=case_id,protocol=PROTOCOL,
                   metrics=count_matches(len(gold),len(candidates),edges),decisions=decisions)
        save(output,value);return value
    except Exception as exc:
        save(evidence/'failure.json',dict(status='failed',error_type=type(exc).__name__))
        raise
