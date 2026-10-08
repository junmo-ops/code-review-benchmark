"""Pinned Martian prompt AND evaluate_review; only transport/data adapted here."""
import argparse,asyncio,hashlib,importlib.util,json,math,os,sys
from pathlib import Path
from runner.storage import ROOT,save,validate_answer
from suites.registry import resolve_run_suite
from .registry import verify

PATH=ROOT/'vendor/martian/offline/code_review_benchmark/step3_judge_comments.py'
spec=importlib.util.spec_from_file_location('martian_offline_upstream',PATH)
upstream=importlib.util.module_from_spec(spec);sys.modules[spec.name]=upstream;spec.loader.exec_module(upstream)


def golden_rows(row,suite_id):
    if suite_id=='aacr':comments=[{'comment':c['text'],'path':c.get('path'),'line':c.get('start_line')} for c in row['reference_comments'] if c.get('text')]
    else:
        ann=row['annotation'];ids=set(ann.get('substantive_comment_ids',[]))
        selected=[c for c in ann['comments'] if c.get('comment_id') in ids] if ids else [c for c in ann['comments'] if c.get('is_initiating_comment')]
        comments=[{'comment':c['body'],'severity':c.get('severity'),'category':c.get('category'),'path':c.get('file'),'line':c.get('line')} for c in selected if c.get('body')]
    if not comments:raise ValueError('参考意见为空，拒绝生成空评分')
    return comments


def validate_result(data):
    if not isinstance(data,dict) or type(data.get('match')) is not bool:raise ValueError('裁判 match 必须为布尔值')
    confidence=data.get('confidence')
    if type(confidence) not in (int,float) or not math.isfinite(confidence) or not 0<=confidence<=1:raise ValueError('裁判 confidence 无效')
    if not isinstance(data.get('reasoning'),str) or not data['reasoning'].strip():raise ValueError('裁判缺少理由')
    return data


class Judge(upstream.LLMJudge):
    def __init__(self,client,model,run,parameters):
        # Avoid upstream .env loading and endpoint/key printing.
        self.client,self.model,self.run,self.parameters=client,model,run,parameters
        self.lock=asyncio.Lock();self.counter=0

    async def call_llm(self,prompt,max_retries=0):
        async with self.lock:
            self.counter+=1;prefix=self.run/'judge'/f'{self.counter:06d}'
            kwargs={'model':self.model,'temperature':self.parameters['temperature'],
                    'max_tokens':self.parameters['max_tokens'],
                    'messages':[{'role':'system','content':'You are a precise code review evaluator. Always respond with valid JSON.'},
                                {'role':'user','content':prompt}]}
            save(prefix.with_suffix('.request.json'),kwargs)
            try:
                response=await self.client.chat.completions.create(**kwargs)
                save(prefix.with_suffix('.response.json'),response.model_dump())
                if not response.choices or response.choices[0].finish_reason!='stop':raise ValueError('裁判输出截断或不完整')
                content=response.choices[0].message.content or ''
                content=content.strip()
                if content.startswith('```'):
                    content=content.split('```')[1]
                    if content.startswith('json'):content=content[4:]
                result=validate_result(json.loads(content))
                save(prefix.with_suffix('.decision.json'),result)
                return result
            except Exception as error:
                # No credential-bearing transport exception text in local error files.
                save(prefix.with_suffix('.failure.json'),{'error_type':type(error).__name__})
                raise


async def evaluate(run,config):
    from openai import AsyncOpenAI
    suite=resolve_run_suite(config);verify(config)
    for key in ('JUDGE_API_KEY','JUDGE_BASE_URL','JUDGE_MODEL'):
        if not os.getenv(key):raise ValueError('缺少 '+key)
    if os.getenv('JUDGE_USE_MOCK','false').lower() not in ('false','0','no',''):raise ValueError('禁止 Mock 评分')
    if os.environ['JUDGE_MODEL']!=config['judge_model'] or hashlib.sha256(os.environ['JUDGE_BASE_URL'].encode()).hexdigest()!=config['judge_endpoint_sha256']:raise ValueError('裁判模型或接口与冻结值不同')
    if any((run/p).exists() for p in ('judge','metrics')):raise ValueError('已有评分记录，另建批次，不覆盖证据')
    rows=[json.loads(x) for x in (run/'dataset.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    cases=suite.load_cases(run/'dataset.jsonl');prepared=[]
    for row,case in zip(rows,cases):
        answer=validate_answer(case,run/'results'/f'{case.safe_id}.json')
        candidates=[c['content'] for c in answer['review']['comments']]
        if any(not isinstance(c,str) or not c.strip() for c in candidates):raise ValueError('答卷含空意见')
        prepared.append((case,golden_rows(row,suite.id),candidates))
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
                    'duplicate_reference_texts':len(gold)-len({c['comment'] for c in gold}),
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


def report(run,config):
    data=json.loads((run/'metrics/martian.json').read_text(encoding='utf-8'))
    if data['status']!='complete':raise ValueError('没有完整评分')
    s=data['summary'];lines=['# Martian Offline 裁判报告','',f'批次：`{run.name}`','',
        f"案例集：`{data['suite_id']}`；裁判模型：`{data['judge_model']}`；状态：完成。",'',
        '复用 Martian 固定版本的 Offline 匹配提示词和 evaluate_review 评分函数。输入是已有结构化意见，未执行其 GitHub 抓取、意见提取或语义去重步骤；全部参考意见参与评分，不套用其分类筛选。',
        '这是原案例数据上的内部对照协议，不是 Martian 官方数据集成绩。裁判只比较两段意见文本，不以行号过滤，不读取 diff。','',
        '| 指标 | 值 |','|---|---:|',f"| 检视意见 | {s['total_candidates']} |",f"| 参考意见 | {s['total_golden']} |",f"| 命中参考（上游 TP） | {s['tp']} |",
        f"| 精确率（上游口径） | {s['precision']:.1%} |",f"| 召回率 | {s['recall']:.1%} |",f"| F1（由 P/R 推导） | {s['f1']:.3f} |",'',
        '上游 TP 按命中的参考意见计数，没有强制一对一；一条检视意见命中多个参考时，精确率可能超过 100%。报告保留该口径，不截断、不冒充一对一指标。相同文本会受上游字典合并行为影响。',
        '上游 false_positives 表示未匹配参考，不能直接认定为事实误报。与原始裁判对比时，分数差异同时包含位置筛选、提示词、匹配和计数规则差异。','']
    for row in data['results']:
        lines += [f"## {row['instance_id']}",'',f"重复参考文本：{row['duplicate_reference_texts']}；重复检视文本：{row['duplicate_candidate_texts']}。",'']
        for hit in row['metrics']['true_positives']:
            lines += ['### 命中','',hit['golden_comment'],'',hit['matched_candidate'],'',f"裁判原文：{hit.get('reasoning','')}",'']
        for miss in row['metrics']['false_negatives']:lines+=['### 未命中参考','',miss['golden_comment'],'']
        for unmatched in row['metrics']['false_positives']:lines+=['### 未匹配检视意见','',unmatched['candidate'],'']
    path=run/'report.zh-CN.md';path.write_text('\n'.join(lines)+'\n',encoding='utf-8');print('中文报告：',path)


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['eval','report']);p.add_argument('--run',type=Path,required=True);a=p.parse_args()
    config=json.loads((a.run/'run-config.json').read_text(encoding='utf-8'));resolve_run_suite(config)
    if verify(config)!='martian':raise ValueError('不是 Martian 批次')
    if hashlib.sha256((a.run/'dataset.jsonl').read_bytes()).hexdigest()!=config['dataset_sha256']:raise ValueError('数据已变化')
    if a.stage=='eval':asyncio.run(evaluate(a.run,config))
    else:report(a.run,config)
if __name__=='__main__':main()
