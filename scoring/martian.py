"""Martian driver for suite-provided references; frozen v1 remains usable."""
import argparse, asyncio, hashlib, json, os
from pathlib import Path
from runner.storage import ROOT, save, validate_answer
from suites.registry import resolve_run_suite
from evaluators.martian import Judge, upstream, report
from .registry import verify

async def evaluate(run,config):
    from openai import AsyncOpenAI
    suite=resolve_run_suite(config);verify(config)
    for key in ('JUDGE_API_KEY','JUDGE_BASE_URL','JUDGE_MODEL'):
        if not os.getenv(key):raise ValueError('缺少 '+key)
    if os.getenv('JUDGE_USE_MOCK','false').lower() not in ('false','0','no',''):raise ValueError('禁止 Mock 评分')
    if os.environ['JUDGE_MODEL']!=config['judge_model'] or hashlib.sha256(os.environ['JUDGE_BASE_URL'].encode()).hexdigest()!=config['judge_endpoint_sha256']:raise ValueError('裁判模型或接口与冻结值不同')
    if any((run/p).exists() for p in ('judge','metrics')):raise ValueError('已有评分记录，另建批次，不覆盖证据')
    with (run/'dataset.jsonl').open(encoding='utf-8') as stream:
        rows=[json.loads(x) for x in stream if x.strip()]
    cases=suite.load_cases(run/'dataset.jsonl');prepared=[]
    for row,case in zip(rows,cases):
        answer=validate_answer(case,run/'results'/f'{case.safe_id}.json')
        candidates=[c['content'] for c in answer['review']['comments']]
        if any(not isinstance(c,str) or not c.strip() for c in candidates):raise ValueError('答卷含空意见')
        prepared.append((case,suite.reference_comments(row),candidates))
    row_metadata={row['instance_id']:row.get('metadata',{}) for row in rows}
    parameters=config['evaluator']['parameters'];results=[]
    save(run/'evaluation-config.json',config['evaluator'])
    try:
        async with AsyncOpenAI(api_key=os.environ['JUDGE_API_KEY'],base_url=os.environ['JUDGE_BASE_URL'],timeout=parameters['timeout_seconds'],max_retries=0) as client:
            judge=Judge(client,config['judge_model'],run,parameters)
            for case,gold,candidates in prepared:
                start=judge.counter+1
                result=await upstream.evaluate_review(judge,gold,candidates)
                # Upstream retains per-pair failures but still calculates partial scores.
                # Keep raw result; never publish those as complete metrics here.
                save(run/'judge'/f'{case.safe_id}.upstream.json',result)
                if result.get('errors_count') or result.get('errors') or result.get('skipped'):raise ValueError('裁判部分失败或无参考答案，停止计分')
                results.append({'instance_id':case.instance_id,'metrics':result,'request_range':[start,judge.counter],
                    'metadata':row_metadata.get(case.instance_id,{}), 'duplicate_reference_texts':len(gold)-len({c['comment'] for c in gold}),
                    'duplicate_candidate_texts':len(candidates)-len(set(candidates))})
        summary={key:sum(row['metrics'][key] for row in results) for key in ('tp','fp','fn','total_candidates','total_golden')}
        summary['precision']=summary['tp']/summary['total_candidates'] if summary['total_candidates'] else 0.
        summary['recall']=summary['tp']/summary['total_golden'] if summary['total_golden'] else 0.
        p,r=summary['precision'],summary['recall'];summary['f1']=2*p*r/(p+r) if p+r else 0.
        save(run/'metrics/martian.json',{'status':'complete','suite_id':suite.id,'evaluator':'martian_offline',
            'protocol':parameters,'judge_model':config['judge_model'],'summary':summary,'results':results})
    except Exception as error:
        save(run/'metrics/martian.failure.json',{'status':'failed','error_type':type(error).__name__,'completed_cases':[r['instance_id'] for r in results]})
        raise
    report(run,config)


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['eval','report']);p.add_argument('--run',type=Path,required=True);a=p.parse_args()
    config=json.loads((a.run/'run-config.json').read_text(encoding='utf-8'));resolve_run_suite(config)
    if verify(config)!='martian':raise ValueError('不是 Martian 批次')
    if hashlib.sha256((a.run/'dataset.jsonl').read_bytes()).hexdigest()!=config['dataset_sha256']:raise ValueError('数据已变化')
    if a.stage=='eval':asyncio.run(evaluate(a.run,config))
    else:report(a.run,config)
if __name__=='__main__':main()
