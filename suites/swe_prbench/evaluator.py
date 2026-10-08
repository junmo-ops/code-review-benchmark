"""Reuse the pinned upstream judge prompt, matching and dimension scorer verbatim."""
import hashlib
import importlib.util
import json
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path
from runner.storage import ROOT,save,validate_answer
from .adapter import records,SWEPRBenchSuite
sys.path.insert(0,str(ROOT/'vendor/swe_prbench'))
from eval_harness.schema import AgentComment,AgentOutput,EvalInput
from eval_harness.judge import run_judge,_parse_json_response
from eval_harness.scorer import compute_dimension_scores
from eval_harness.matching import normalize_judge_alignment


def make_input(row):
    ann=row['annotation']; ids=set(ann.get('substantive_comment_ids',[]))
    human=[c for c in ann['comments'] if c.get('comment_id') in ids] if ids else [c for c in ann['comments'] if c.get('is_initiating_comment')]
    if not human or len({c['comment_id'] for c in human})!=len(human):
        raise ValueError('人工标注为空或 ID 重复')
    # No frozen A/B/C agent context is claimed for the repository reviewer.
    return EvalInput(row['task_id'],int(row['pr_number']),row['repo'],'lcr_repository_variant','',0,
                     row['contexts']['A'].get('pipeline_version',''),row.get('difficulty',''),row.get('pr_type',''),
                     row.get('language',''),row['diff_patch'],human,any(c.get('severity') for c in human))


def changed_lines(diff):
    result={};path=None;line=0
    for text in diff.splitlines():
        if text.startswith('+++ b/'):
            path=text[6:];result.setdefault(path,set())
        elif text.startswith('@@'):
            match=re.search(r'\+(\d+)(?:,\d+)? @@',text)
            if match:line=int(match.group(1))
        elif path and text.startswith('+'):
            result[path].add(line);line+=1
        elif text.startswith(' '):line+=1
    return result


def make_output(row,answer,model):
    lines=changed_lines(row['diff_patch'])
    comments=[AgentComment(f'a_{i+1}',c['content'],c.get('path'),c.get('start_line'),None,
                           c.get('start_line') not in lines.get(c.get('path'),set()))
              for i,c in enumerate(answer['review']['comments'])]
    return AgentOutput(row['task_id'],'lcr_repository_variant',model,json.dumps(answer,ensure_ascii=False),comments,True,None)


def validate_judge(data,eval_input,agent_output):
    agents={c.comment_id for c in agent_output.comments};humans={c['comment_id'] for c in eval_input.human_comments}
    ac=data.get('agent_classifications');hs=data.get('human_comment_statuses')
    for rows,expected in ((ac,agents),(hs,humans)):
        if not isinstance(rows,list) or any(not isinstance(x,dict) for x in rows) or len(rows)!=len(expected) or {x.get('comment_id') for x in rows}!=expected:
            raise ValueError('裁判返回的 ID 不完整或重复，停止评分')
    matches={}
    for c in ac:
        if c.get('classification') not in ('CONFIRMED','PLAUSIBLE','FABRICATED') or type(c.get('actionability_score')) is not int or not 1<=c['actionability_score']<=5:
            raise ValueError('裁判分类或可操作性分数无效')
        matched=c.get('matched_human_comment_id')
        if c['classification']=='CONFIRMED':
            if matched not in humans:raise ValueError('裁判匹配了不存在的人工标注')
            matches[c['comment_id']]=matched
        elif matched is not None:raise ValueError('非 CONFIRMED 意见不能匹配人工标注')
    for h in hs:
        caught=h['comment_id'] in matches.values()
        if h.get('status')!=('CAUGHT' if caught else 'MISSED'):
            raise ValueError('裁判两侧匹配状态矛盾')
        if caught and matches.get(h.get('matched_agent_comment_id'))!=h['comment_id']:
            raise ValueError('裁判两侧匹配 ID 矛盾')
        if not caught and h.get('matched_agent_comment_id') is not None:raise ValueError('MISSED 不应包含匹配 ID')


class JudgeRouter:
    def __init__(self,run,eval_input,agent_output,config):
        from openai import OpenAI
        if os.getenv('JUDGE_USE_MOCK','false').lower() not in ('false','0','no',''):
            raise ValueError('禁止 Mock 裁判')
        key=os.environ.get('JUDGE_API_KEY');url=os.environ.get('JUDGE_BASE_URL');model=os.environ.get('JUDGE_MODEL')
        if not key or not url or not model:raise ValueError('缺少 JUDGE 配置')
        if model!=config['judge_model'] or hashlib.sha256(url.encode()).hexdigest()!=config['judge_endpoint_sha256']:
            raise ValueError('裁判配置与批次冻结值不一致')
        self.client=OpenAI(api_key=key,base_url=url,timeout=180,max_retries=0)
        self.run,self.input,self.output=run,eval_input,agent_output

    def generate(self,model_id,system,user,max_tokens,**kwargs):
        prefix=self.run/'judge'/self.input.task_id
        save(prefix.with_suffix('.request.json'),{'model':model_id,'messages':[{'role':'system','content':system},{'role':'user','content':user}],'temperature':0,'max_tokens':max_tokens})
        response=self.client.chat.completions.create(model=model_id,messages=[{'role':'system','content':system},{'role':'user','content':user}],temperature=0,max_tokens=max_tokens)
        save(prefix.with_suffix('.response.json'),response.model_dump())
        if not response.choices or response.choices[0].finish_reason!='stop':raise ValueError('裁判响应不完整')
        raw=response.choices[0].message.content or ''
        validate_judge(_parse_json_response(raw),self.input,self.output)
        return raw


def evaluate(run,config):
    # Pin the upstream's built-in lexical fallback deliberately. Installing an
    # embedding package must not silently change scores for the same frozen run.
    if importlib.util.find_spec('sentence_transformers') is not None:
        raise ValueError('本适配协议固定 upstream lexical fallback；请使用项目隔离环境，不能静默切换评分后端')
    output=run/'metrics/swe_prbench.json'
    if output.exists():raise ValueError('已有评分；请使用 report 重建报告或另建批次，避免覆盖裁判证据')
    rows=records(run/'dataset.jsonl');cases=SWEPRBenchSuite().load_cases(run/'dataset.jsonl'); results=[]
    try:
        for row,case in zip(rows,cases):
            answer=validate_answer(case,run/'results'/f'{case.safe_id}.json')
            inp=make_input(row);out=make_output(row,answer,config['review_model'])
            router=JudgeRouter(run,inp,out,config)
            judged=run_judge(inp,out,config['judge_model'],router)
            save(run/'judge'/f'{case.safe_id}.judged.json',asdict(judged))
            if judged.judge_prompt_version.endswith('+fallback'):raise ValueError('上游裁判降级，停止评分；详见 judge 原始响应')
            validate_judge(asdict(judged),inp,out)
            metrics=compute_dimension_scores(inp,out,judged)
            alignment=normalize_judge_alignment(inp,out,judged)
            results.append({'task_id':case.instance_id,'metrics':metrics,'human_comments':inp.human_comments,
                            'agent_comments':[asdict(c) for c in out.comments],'judge':asdict(judged),
                            'alignment':asdict(alignment),'review_summary':answer['review'].get('summary',{})})
        save(output,{'status':'complete','suite':'swe_prbench','protocol':config['suite']['parameters'],
                     'review_model':config['review_model'],'judge_model':config['judge_model'],'results':results})
        report(run,config)
    except Exception as error:
        save(run/'metrics/swe_prbench.failure.json',{'status':'failed','error_type':type(error).__name__})
        raise


def report(run,config):
    data=json.loads((run/'metrics/swe_prbench.json').read_text(encoding='utf-8'))
    if data.get('status')!='complete':raise ValueError('没有完整评分')
    text=['# SWE-PRBench 测评报告','',f'批次：`{run.name}`','',
          '**评分状态：完成。验证范围：单例完整流程；不代表整套案例集成绩。**','',
          '协议：LCR 完整仓库检视适配实验。使用官方案例、人工标注、裁判提示词及维度评分源码；未采用官方 A/B/C 固定输入限制。',
          f"检视模型：`{data['review_model']}`；裁判模型：`{data['judge_model']}`；裁判 1 轮。",
          '语义匹配使用上游内置词面回退（未加载 all-mpnet-base-v2），中英文词面相似度存在局限。该配置不等同于官方嵌入评分；不生成官方总分，不与官方榜单或 AACR 横向混算。','',
          '中文用于说明；下列原始标注和裁判理由保留原文，不额外调用翻译模型。','']
    labels={'precision':'确认意见精确率','recall':'人工问题召回率','f1_score':'F1','hallucination_rate':'虚构意见比例','actionability_score':'可操作性（归一化）','confirmed_count':'确认意见数','plausible_count':'合理但未匹配意见数','fabricated_count':'虚构意见数'}
    for row in data['results']:
        text += [f"## 案例 `{row['task_id']}`",'', '| 指标 | 值 |','|---|---:|']
        text += [f"| {label} | {row['metrics'][key]:.3f} |" for key,label in labels.items()]
        text += ['',f"检视耗时：{row['review_summary'].get('elapsed','未提供')}；人工标注 {len(row['human_comments'])} 条，检视意见 {len(row['agent_comments'])} 条。",'', '### 检视意见与裁判','']
        decisions={d['comment_id']:d for d in row['judge']['agent_classifications']}
        for c in row['agent_comments']:
            d=decisions[c['comment_id']];label={'CONFIRMED':'匹配人工问题','PLAUSIBLE':'合理但未匹配人工问题','FABRICATED':'存在事实错误'}[d['classification']]
            text += [f"**{c['comment_id']} · {c['file_reference']}:{c['line_reference']} · {label}**",'',c['body'],'',f"裁判原始理由：{d.get('reasoning') or '无'}",'']
        text += ['### 人工参考意见','']
        for h in row['human_comments']:text += [f"**{h['comment_id']} · {h.get('file')}:{h.get('line')}**",'',h.get('body',''),'']
        text += ['评分数值来自上游匹配归一化后的结果；裁判原始分类保存在 judge/，归一化配对保存在 metrics/swe_prbench.json。','']
    path=run/'report.zh-CN.md';path.write_text('\n'.join(text)+'\n',encoding='utf-8');print('中文报告：',path)
