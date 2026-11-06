"""Add execution facts to ordinary reports without changing either scoring protocol."""
import json
from .gateway import summarize
from runner import case_attempts

MARKER = '\n## DSH 检视执行条件\n'


def completion(run, items):
    from runner.cloud import states
    from webui.scoring_scope import scored_case_ids
    statuses = {row['instance_id']: row['status'] for row in states(items, run)}
    scored = scored_case_ids(run)
    complete = sum(status == 'complete' for status in statuses.values())
    failed = sum(status in {'failed', 'unsupported', 'invalid_answer', 'exited'} for status in statuses.values())
    timed_out = sum(status == 'timed_out' for status in statuses.values())
    scope = '未提供' if scored is None else str(len(scored))
    text = f'完整答卷 {complete}/{len(items)}；失败 {failed}；超时 {timed_out}；实际评分 {scope}/{len(items)}。'
    return text, statuses, scored


def subject(cfg):
    if cfg['skill_mode'] == 'baseline':
        return '无待测 Skill 基线'
    return 'Skill ' + cfg['skill_identity']['name'] + '；内容版本 ' + cfg['skill_version']


def execution_conditions(cfg):
    tools = cfg['dsh_policy'].get('tools', [])
    if cfg['dsh_policy'].get('runtime_mode') == 'pod':
        isolation = ('POD 内运行（无操作系统级沙盒）；不保证阻止进程访问同容器数据或网络。'
                     'bash 命令不受文件工具路径检查约束。')
    else:
        access = '案例快照可写' if 'write' in tools else '案例快照只读'
        isolation = 'Bubblewrap 沙盒；独立进程、挂载和网络命名空间；' + access + '。'
    return isolation + ' 不同权限、重试或隔离条件的结果不能全部归因于 Skill 本身。'


def retry_conditions(cfg):
    policy = cfg.get('case_execution_policy', {})
    attempts = policy.get('max_attempts')
    label = str(attempts) if attempts is not None else '历史批次未记录'
    runtime_policy = cfg['dsh_policy']
    return (f"整例预算 {cfg['timeout_minutes']} 分钟；案例最多尝试 {label} 次；新策略下多次尝试共享整例预算。"
            f"模型请求重试 {runtime_policy.get('request_retries', '未记录')} 次，"
            f"压缩重试 {runtime_policy.get('compaction_retries', '未记录')} 次，"
            f"溢出恢复 {runtime_policy.get('overflow_retries', '未记录')} 次。"
            '仅答卷格式失败允许整例重试；失败尝试的已知用量一并累计。')


def annotate(run, cfg, items):
    path = run / 'report.zh-CN.md'
    if not path.exists():
        return
    text, statuses, scored = completion(run, items)
    lines = [MARKER, '', subject(cfg) + '；DSH ' + cfg['tool_hashes']['version'] + '。',
             text,
             execution_conditions(cfg),
             '每案例全新会话，head 快照与 merge-base diff；工具：' + ' / '.join(cfg['dsh_policy']['tools']) + '。',
             retry_conditions(cfg),
             f"单次模型输出上限 {cfg['dsh_policy']['max_output_tokens']} tokens；上下文窗口 {cfg['dsh_policy']['context_window']} tokens。", '',
             '| 案例 | 检视状态 / 评分范围 | 已知检视用量 | 请求覆盖 | 检视秒数 | 清理秒数 | Skill 加载 / 能力 |',
             '|---|---|---|---|---:|---:|---|']
    for item in items:
        lines.append(_case_line(run, item, statuses[item.instance_id], scored))
    judge = _judge_records(run)
    usage, coverage = summarize(judge)
    lines += ['', '裁判已知用量（与检视分开）：' + json.dumps(usage, ensure_ascii=False),
              '裁判请求覆盖：' + json.dumps(coverage),
              '缺失用量不解释成零；缓存读取包含在输入中，不额外累加。复用已保存请求不重复计数。',
              '执行器、Node、依赖锁及适配器身份见 run-config.json；脱敏事件、请求与完整性证据见 raw/。',
              '本地替身验收仅证明流程，不代表真实模型检视质量或行内镜像已验收。', '']
    original = path.read_text(encoding='utf-8').split(MARKER)[0]
    path.write_text(original + '\n'.join(lines), encoding='utf-8')


def _case_line(run, item, status, scored):
    execution = run / 'raw' / (item.safe_id + '.execution.json')
    record = json.loads(execution.read_text()) if execution.exists() else {}
    accounting = case_attempts.totals(run, item.safe_id)
    elapsed = (accounting['elapsed_seconds'] if accounting['accounting_complete']
               else record.get('supervised_seconds', '未提供'))
    cleanup = record.get('cleanup_seconds', '未提供')
    scope = '评分范围未提供' if scored is None else '已评分' if item.instance_id in scored else '未评分 / 排除'
    count = accounting['attempt_count'] or '历史未记录'
    prefix = f'| {item.instance_id} | {status}；{scope}；尝试 {count} 次 | '
    path = run / 'results' / (item.safe_id + '.json')
    if not path.exists():
        records = case_attempts.request_records(run, item.safe_id)
        usage, coverage = summarize(records)
        return prefix + f'{json.dumps(usage)} | {coverage} | {elapsed} | {cleanup} | {_support(run, item)} |'
    answer = json.loads(path.read_text())
    review = answer['review']
    return (prefix + f"{json.dumps(review['summary'])} | {review['usage_coverage']} | {elapsed} | {cleanup} | "
            f"{_loading(answer)} |")


def _loading(answer):
    skill = answer['skill']
    if skill['mode'] == 'baseline':
        return '基线，不加载'
    forms = ', '.join(sorted({entry['form'] for entry in skill['evidence']}))
    return '完整加载已核验：' + forms


def _support(run, item):
    path = run / 'raw' / (item.safe_id + '.skill-support.json')
    if path.exists() and json.loads(path.read_text()).get('status') == 'unsupported':
        return '不支持所需能力，依据见 raw/' + path.name
    return '加载或执行未完成，见 raw/'


def _judge_records(run):
    path = run / 'judge-requests.json'
    if path.exists():
        return json.loads(path.read_text())
    return [json.loads(p.read_text()) for p in sorted((run / 'judge').glob('*.response.json'))]
