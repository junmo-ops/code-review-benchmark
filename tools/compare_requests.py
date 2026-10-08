"""Offline request comparison. No API calls; never export message/code content."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics

PARAMS = ('model', 'stream', 'max_tokens', 'max_completion_tokens', 'temperature',
          'top_p', 'reasoning_effort', 'enable_thinking', 'thinking', 'tool_choice')


def sources(record):
    yield '', record
    for key in ('request', 'params', 'parameters', 'body', 'options'):
        value = record.get(key)
        if isinstance(value, dict):
            yield key+'.', value
            for nested in ('body', 'params', 'parameters'):
                if isinstance(value.get(nested), dict):
                    yield key+'.'+nested+'.', value[nested]


def request_metadata(record):
    params = {}
    messages = None
    origin = None
    for prefix, source in sources(record):
        for key in PARAMS:
            value = source.get(key)
            # Only scalars; no arbitrary nested payloads or credentials.
            if key in source and (value is None or isinstance(value, (str, int, float, bool))):
                params[prefix+key] = value if not isinstance(value, str) else value[:100]
        if messages is None and isinstance(source.get('messages'), list):
            messages = source['messages']; origin = prefix+'messages'
    result = dict(file=record.get('filePath'), task=record.get('taskType'),
                  request_no=record.get('request_no'), time=record.get('timestamp'),
                  parameters=params, logged_keys=sorted(record), status='未匹配返回记录')
    if messages is not None:
        serialized = json.dumps(messages, ensure_ascii=False, separators=(',', ':'))
        tools = [m for m in messages if isinstance(m, dict) and m.get('role') == 'tool']
        result.update(messages_field=origin, message_count=len(messages),
                      messages_serialized_chars=len(serialized),
                      messages_serialized_bytes=len(serialized.encode('utf-8')),
                      tool_message_count=len(tools),
                      tool_content_chars=sum(len(json.dumps(m.get('content'), ensure_ascii=False)) for m in tools))
    return result


def analyse(path):
    requests=[]; by_id={}; pending={}; unpaired=0; invalid=0
    tool_names=Counter(); repeated=Counter(); call_names={}; tool_durations=[]
    with path.open(encoding='utf-8-sig') as source:
        for line in source:
            try:
                record=json.loads(line)
                if not isinstance(record,dict): raise ValueError()
            except ValueError:
                invalid+=1; continue
            kind=record.get('type')
            if kind=='llm_request':
                meta=request_metadata(record); requests.append(meta)
                if isinstance(record.get('uuid'),str): by_id[record['uuid']]=meta
                pending.setdefault((meta['file'],meta['task']),[]).append(meta)
            elif kind in ('llm_response','llm_error'):
                meta=by_id.get(record.get('parentUuid')); method='parentUuid'
                if meta is None:
                    candidates=[m for m in pending.get((record.get('filePath'),record.get('taskType')),[]) if m['status']=='未匹配返回记录']
                    if len(candidates)==1:
                        meta=candidates[0];method='同文件同任务仅一个待返回请求（推断）'
                if meta is None or meta['status']!='未匹配返回记录':
                    unpaired+=1
                else:
                    error=str(record.get('error',''))
                    meta.update(status='504' if kind=='llm_error' and '504' in error else '请求失败' if kind=='llm_error' else '返回成功',
                                duration_ms=record.get('duration_ms'), response_time=record.get('timestamp'),match_method=method)
                    usage=record.get('usage')
                    if isinstance(usage,dict):
                        meta['usage']={k:v for k,v in usage.items() if isinstance(v,(int,float))}
                for call in record.get('tool_calls') or []:
                    if isinstance(call,dict):
                        func=call.get('function') if isinstance(call.get('function'),dict) else call
                        call_names[call.get('id')]=func.get('name')
            elif kind=='tool_call':
                name=record.get('name') or record.get('tool_name') or record.get('toolName') or record.get('tool')
                if not isinstance(name,str): name=call_names.get(record.get('tool_call_id') or record.get('id'))
                name=name or '名称未记录'
                tool_names[name]+=1
                args=record.get('arguments')
                if args is not None:
                    # Repetition fingerprint only, never print arbitrary arguments.
                    fingerprint=hashlib.sha256(json.dumps(args,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                    repeated[(name,fingerprint)]+=1
                duration=record.get('duration_ms')
                if isinstance(duration,(int,float)):tool_durations.append(duration)
    return dict(requests=requests,invalid_lines=invalid,unpaired_responses=unpaired,
                tool_counts=dict(tool_names), repeated_tool_argument_groups=sum(n>1 for n in repeated.values()),
                max_identical_tool_calls=max(repeated.values(),default=0),
                recorded_tool_duration_ms=sum(tool_durations))


def write_report(run, cases):
    lines=['# LCR 请求离线对比','',f'轮次：`{run.name}`','',
           '只分析已有会话，不调用模型。上下文长度是对日志 messages 重新序列化的长度，不是 Token 数或实际网络请求大小。缺少参数表示日志未记录，不能按默认值猜测。', '',
           '| 案例 | 完整答卷状态 | 请求记录 | 504 | 已记录 messages 最大字符数 | 成功返回的最大输入 Token |',
           '|---|---|---:|---:|---:|---:|']
    def value(v): return str(v if v is not None else '未记录').replace('|','\\|').replace('\n',' ')
    for case in cases:
        reqs=case.get('analysis',{}).get('requests',[])
        chars=[r['messages_serialized_chars'] for r in reqs if 'messages_serialized_chars' in r]
        tokens=[r.get('usage',{}).get('prompt_tokens') for r in reqs if r['status']=='返回成功']
        tokens=[t for t in tokens if isinstance(t,(int,float))]
        lines.append('| '+' | '.join(map(value,[case['instance_id'],case['status'],len(reqs),sum(r['status']=='504' for r in reqs),max(chars,default=None),max(tokens,default=None)]))+' |')
    for case in cases:
        lines+=['', '## '+case['instance_id'],'', '会话匹配：'+case['match'],'']
        data=case.get('analysis')
        if not data:continue
        reqs=data['requests']; durations=[r['duration_ms'] for r in reqs if isinstance(r.get('duration_ms'),(int,float))]
        lines += [f"已记录请求耗时合计：{sum(durations)/1000:.3f} 秒；中位数：{statistics.median(durations)/1000 if durations else '未记录'} 秒。合计不等于并发墙钟耗时。",'',
                  f"工具事件计数：{data['tool_counts']}；相同工具参数重复组数：{data['repeated_tool_argument_groups']}；最大重复次数：{data['max_identical_tool_calls']}。重复不等于无效循环。",'',
                  f"未匹配响应：{data['unpaired_responses']}；无法解析行数：{data['invalid_lines']}。",'',
                  '| 文件 / 任务 | 请求序号 | 状态 | 耗时秒 | 消息条数 | messages 字符数 | 工具内容字符数 | 输入 / 输出 Token |',
                  '|---|---:|---|---:|---:|---:|---:|---|']
        for r in reqs:
            usage=r.get('usage',{})
            lines.append('| '+' | '.join(value(v) for v in [str(r['file'])+' / '+str(r['task']),r['request_no'],r['status'],r['duration_ms']/1000 if isinstance(r.get('duration_ms'),(int,float)) else None,r.get('message_count'),r.get('messages_serialized_chars'),r.get('tool_content_chars'),str(usage.get('prompt_tokens','未记录'))+' / '+str(usage.get('completion_tokens','未记录'))])+' |')
        params=[]
        for r in reqs:
            if r['parameters'] not in params:params.append(r['parameters'])
        lines+=['','日志记录的参数组合：','```json',json.dumps(params,ensure_ascii=False,indent=2),'```','']
        failures=[r for r in reqs if r['status'] in ('504','请求失败')]
        if failures:
            lines+=['失败请求元数据（含精确配对或推断配对标记）：','```json',json.dumps(failures,ensure_ascii=False,indent=2),'```','']
    lines+=['## 判断边界','', '成功和失败案例不同，差异不能单独证明因果。504 请求通常没有 usage，不能把上一次成功请求的 Token 当作失败请求 Token。参数未记录时无法判断流式或输出上限；不自动补默认值。工具参数只用于本地指纹统计，报告不包含完整提示词、代码和工具参数。', '']
    (run/'request-comparison.zh-CN.md').write_text('\n'.join(lines),encoding='utf-8')
    (run/'request-comparison.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description='对比本轮已有模型请求，无模型调用')
    parser.add_argument('--run-id',required=True);args=parser.parse_args()
    if not args.run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.run_id):parser.error('run-id 非法')
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from runner.storage import ROOT
    from suites.registry import resolve_run_suite
    from reviewers.lcr.logs import inspect_case
    root=ROOT/'runs'/args.run_id
    config=json.loads((root/'run-config.json').read_text(encoding='utf-8-sig'))
    dataset=root/'dataset.jsonl'
    if config.get('dataset_sha256') != hashlib.sha256(dataset.read_bytes()).hexdigest():parser.error('案例集与本轮不一致')
    suite=resolve_run_suite(config,verify=False)
    cases=[]
    for item in suite.load_cases(dataset):
        if item.instance_id not in config['selected_instances']:continue
        row=inspect_case(item,root); evidence=row['session_evidence']
        case=dict(instance_id=item.instance_id,status=row['status'],match=evidence['match'])
        if evidence['match']=='matched':case['analysis']=analyse(Path(evidence['source_file']))
        cases.append(case)
    write_report(root,cases)
    print('离线对比完成（未调用模型）：',root/'request-comparison.zh-CN.md')


if __name__=='__main__':main()
