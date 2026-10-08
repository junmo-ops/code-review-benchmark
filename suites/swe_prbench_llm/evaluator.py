"""Count only validated LLM decisions: no similarity matching or embedding calls."""
import json
from dataclasses import asdict
from runner.storage import save,validate_answer
from suites.swe_prbench.adapter import records
# Reuse data/prompt/strict response validation, not the upstream matching/scorer.
from suites.swe_prbench.evaluator import make_input,make_output,validate_judge,JudgeRouter as BaseJudgeRouter,run_judge
from .adapter import SWELLMSuite


class JudgeRouter(BaseJudgeRouter):
    def __init__(self,run,inp,out,config):
        super().__init__(run,inp,out,config)
        self.output_limit=config['suite']['parameters']['max_tokens']

    def generate(self,model_id,system,user,max_tokens,**kwargs):
        return super().generate(model_id,system,user,self.output_limit,**kwargs)


def score_judgement(inp,out,judgement):
    validate_judge(judgement,inp,out)
    used=set();pairs=[];duplicates=[]
    # Each reference can contribute once. Stable source order, no semantic rerank.
    decisions={d['comment_id']:d for d in judgement['agent_classifications']}
    for comment in out.comments:
        d=decisions[comment.comment_id]
        if d['classification']!='CONFIRMED':continue
        human=d['matched_human_comment_id']
        if human in used:
            duplicates.append(comment.comment_id);continue
        used.add(human);pairs.append({'agent_id':comment.comment_id,'human_id':human})
    n=len(out.comments);m=len(inp.human_comments);hits=len(pairs)
    precision=hits/n if n else 0.;recall=hits/m if m else 0.
    counts={label:sum(d['classification']==label for d in decisions.values()) for label in ('CONFIRMED','PLAUSIBLE','FABRICATED')}
    return {'precision':precision,'recall':recall,'f1':2*precision*recall/(precision+recall) if precision+recall else 0.,
            'matched_count':hits,'reference_count':m,'finding_count':n,
            'raw_classification_counts':counts,'duplicate_confirmed_ids':duplicates,'matches':pairs,
            'judge_fabricated_rate':counts['FABRICATED']/n if n else 0.}


def evaluate(run,config):
    output=run/'metrics/swe_prbench_llm.json'
    if output.exists() or (run/'judge').exists():raise ValueError('已有评分或裁判记录，拒绝覆盖；重试请新建评分批次')
    rows=records(run/'dataset.jsonl');cases=SWELLMSuite().load_cases(run/'dataset.jsonl')
    # Validate every answer before spending any model calls.
    answers=[validate_answer(c,run/'results'/f'{c.safe_id}.json') for c in cases]
    save(run/'evaluation-config.json',{'suite_id':'swe_prbench_llm','parameters':config['suite']['parameters'],
        'judge_model':config['judge_model'],'judge_endpoint_sha256':config['judge_endpoint_sha256'],
        'suite_identity':config['suite']['identity']})
    results=[]
    try:
        for row,case,answer in zip(rows,cases,answers):
            inp=make_input(row);out=make_output(row,answer,config['review_model'])
            inp.config_name=out.config_name='internal_llm_only_v1'
            router=JudgeRouter(run,inp,out,config)
            judged=run_judge(inp,out,config['judge_model'],router)
            judgement=asdict(judged)
            save(run/'judge'/f'{case.safe_id}.judged.json',judgement)
            if judged.judge_prompt_version.endswith('+fallback'):raise ValueError('裁判失败或返回无效，停止计分；检查 judge 原始响应')
            scores=score_judgement(inp,out,judgement)
            results.append({'task_id':case.instance_id,'scores':scores,'judge':judgement,
                'human_comments':inp.human_comments,'agent_comments':[asdict(c) for c in out.comments],
                'review_summary':answer['review'].get('summary',{})})
        totals={k:sum(r['scores'][k] for r in results) for k in ('matched_count','reference_count','finding_count')}
        p=totals['matched_count']/totals['finding_count'] if totals['finding_count'] else 0.
        r=totals['matched_count']/totals['reference_count'] if totals['reference_count'] else 0.
        save(output,{'status':'complete','suite':'swe_prbench_llm','protocol':config['suite']['parameters'],
            'review_model':config['review_model'],'judge_model':config['judge_model'],
            'summary':dict(totals,precision=p,recall=r,f1=2*p*r/(p+r) if p+r else 0.),'results':results})
    except Exception as error:
        save(run/'metrics/swe_prbench_llm.failure.json',{'status':'failed','error_type':type(error).__name__,
              'completed_cases':[r['task_id'] for r in results],'note':'未生成完整质量指标；保留答卷和裁判证据'})
        raise
    report(run,config)


def report(run,config):
    data=json.loads((run/'metrics/swe_prbench_llm.json').read_text(encoding='utf-8'))
    if data.get('status')!='complete':raise ValueError('没有完整评分')
    s=data['summary'];text=['# SWE-PRBench 内部测评报告','',f'批次：`{run.name}`','',
        '**评分状态：完成；仅代表本批次选定案例。**','',
        '用途：比较内部 Agent 工具和模型。使用 SWE-PRBench 案例与人工参考、大模型裁判及本项目计数规则，不是官方榜单协议。',
        '语义判断仅来自大模型。不使用嵌入模型、词面相似度或位置加分进行补判。裁判看到 diff、参考意见和检视意见。',
        f"检视模型：`{data['review_model']}`；裁判：`{data['judge_model']}`；1 轮，temperature=0。",'',
        '| 指标 | 值 |','|---|---:|',f"| 案例数 | {len(data['results'])} |",f"| 检视意见数 | {s['finding_count']} |",
        f"| 人工参考数 | {s['reference_count']} |",f"| 一对一命中数 | {s['matched_count']} |",
        f"| 精确率 | {s['precision']:.1%} |",f"| 召回率 | {s['recall']:.1%} |",f"| F1 | {s['f1']:.3f} |",'',
        '精确率=一对一命中数/检视意见数；召回率=命中数/人工参考数。多条意见指向同一参考只计一次。合理但未命中参考不等于误报；裁判判定也不等于独立缺陷验证。','']
    for row in data['results']:
        text += [f"## 案例 `{row['task_id']}`",'',f"原检视耗时：{row['review_summary'].get('elapsed','未提供')}。",'']
        judged={c['comment_id']:c for c in row['judge']['agent_classifications']}
        for c in row['agent_comments']:
            d=judged[c['comment_id']];label={'CONFIRMED':'匹配人工参考','PLAUSIBLE':'合理但未匹配人工参考','FABRICATED':'裁判认为存在事实错误'}[d['classification']]
            text += [f"### {c['comment_id']} · {label}",'',f"位置：`{c['file_reference']}:{c['line_reference']}`",'',c['body'],'',f"裁判原文：{d.get('reasoning') or '无'}",'']
        text += ['### 人工参考意见','']
        for h in row['human_comments']:text += [f"**{h['comment_id']}**",'',h.get('body',''),'']
        if row['scores']['duplicate_confirmed_ids']:text += ['重复命中、不重复计分：'+', '.join(row['scores']['duplicate_confirmed_ids']),'']
    reuse=run/'answer-reuse.json'
    if reuse.exists():text += ['本轮复用了已有完整答卷，仅重新调用裁判；来源和文件哈希见 answer-reuse.json。','']
    path=run/'report.zh-CN.md';path.write_text('\n'.join(text)+'\n',encoding='utf-8');print('中文报告：',path)
