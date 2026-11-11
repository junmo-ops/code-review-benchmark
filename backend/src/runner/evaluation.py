"""Frozen evaluation intent and append-only execution observations."""
from datetime import datetime, timezone
import json
import time
import uuid
from .storage import save

MODES = {'quality': '检视质量', 'stability': '运行稳定性'}


def mode(value=None):
    value = 'quality' if value is None else value
    if not isinstance(value, str) or value not in MODES:
        raise ValueError('测评模式须为 quality 或 stability')
    return value


def stable(cfg):
    return cfg.get('evaluation_mode') == 'stability'


def freeze(value, suite):
    selected = mode(value)
    if selected == 'stability' and suite in {'mcr', 'ccrab'}:
        raise ValueError('MCR 多轮与 c-CRAB 修复测试暂不支持运行稳定性模式')
    return dict(evaluation_mode=selected, evaluation_protocol=1)


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _usage(run, case_id, result):
    requests = run / 'raw' / (case_id + '.requests.json')
    if requests.exists():
        rows = _read(requests)
        values = [(r.get('usage') or {}).get('total_tokens') for r in rows]
        known = [v for v in values if type(v) is int and v >= 0]
        return (sum(known) if known else None), bool(values) and len(known) == len(values)
    if result is None:
        raw = run / 'raw' / (case_id + '.stdout.json')
        result = _read(raw) if raw.exists() else {}
    answer = result if isinstance(result, dict) else {}
    summary = answer.get('review', answer).get('summary', {}) or {}
    value = summary.get('total_tokens')
    return (value if type(value) is int and value >= 0 else None), False


def observe(cfg, run, case_id, invoke, *, kind='execute', redact=str):
    if not cfg.get('evaluation_protocol'):
        return invoke()
    folder = run / 'evaluation-attempts' / case_id
    folder.mkdir(parents=True, exist_ok=True)
    previous = sorted(folder.glob('*.json'))
    # Evidence created by an older runtime cannot become a new "first" execution.
    legacy = not previous and (run / 'raw' / (case_id + '.execution.json')).exists()
    path = folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex + '.json')
    record = dict(status='running', kind=kind, legacy_predecessor=legacy,
                  trigger=('manual_reexecution' if previous else 'initial') if kind == 'execute' else kind,
                  started_at=datetime.now(timezone.utc).isoformat())
    save(path, record)
    started, result = time.monotonic(), None
    try:
        result = invoke()
        record['status'] = 'simulation_complete' if cfg.get('simulation') else 'succeeded'
        if result is None:
            answer_path = run / 'results' / (case_id + '.json')
            result = _read(answer_path) if answer_path.exists() else None
        if isinstance(result, dict):
            record['raw_format_compliant'] = result.get('review', {}).get('raw_format_compliant')
        return result
    except BaseException as error:
        record.update(status='failed', error_type=type(error).__name__, error=redact(str(error))[:1500],
                      failure_owner=_failure_owner(error))
        raise
    finally:
        record['elapsed_seconds'] = round(time.monotonic() - started, 3)
        try:
            record['known_tokens'], record['token_coverage_complete'] = (None, False) if kind in {'query_recovery', 'session_resume'} else _usage(run, case_id, result)
        except (OSError, ValueError, TypeError, AttributeError):
            record.update(known_tokens=None, token_coverage_complete=False)
        save(path, record)


def _case(run, case_id):
    rows = [_read(p) for p in sorted((run / 'evaluation-attempts' / case_id).glob('*.json'))]
    first = rows[0] if rows and not rows[0].get('legacy_predecessor') and rows[0]['kind'] == 'execute' else None
    return dict(id=case_id, first=first, attempts=rows,
                final_complete=_complete(run / 'results' / (case_id + '.json')))


def _complete(path):
    try:
        review = _read(path).get('review', {})
        return review.get('status') == 'complete' and isinstance(review.get('comments'), list)
    except (OSError, ValueError, AttributeError):
        return False


def _first_finished(row):
    return bool(row and row['status'] in {'succeeded', 'failed'}
                and row.get('failure_owner') not in {'platform', 'interruption'})


def summary(run, cfg, case_ids):
    cases = [_case(run, case_id) for case_id in case_ids]
    known = [c['first'] for c in cases if _first_finished(c['first'])]
    success = sum(r['status'] == 'succeeded' for r in known)
    rows = [r for c in cases for r in c['attempts']]
    tokens = [r['known_tokens'] for r in rows if r.get('known_tokens') is not None]
    first_totals = _totals(known, [r['known_tokens'] for r in known if r.get('known_tokens') is not None])
    totals = _totals(rows, tokens)
    return dict(**totals, first_elapsed_seconds=first_totals['elapsed_seconds'],
                first_known_tokens=first_totals['known_tokens'], mode=cfg.get('evaluation_mode', 'legacy'), total=len(cases), first_observed=len(known),
                first_succeeded=success, first_success_rate=success / len(known) if known else None,
                final_complete=sum(c['final_complete'] for c in cases),
                platform_followups=sum(max(0, len(c['attempts']) - 1) for c in cases),
                cases=cases)


def _failure_owner(error):
    if isinstance(error, (KeyboardInterrupt, SystemExit)) or type(error).__name__ == 'CancelledError':
        return 'interruption'
    if isinstance(error, OSError) or type(error).__name__ == 'IsolationError':
        return 'platform'
    return 'unclassified'


def _totals(rows, tokens):
    return dict(elapsed_seconds=sum(r.get('elapsed_seconds', 0) for r in rows) if rows else None,
                known_tokens=sum(tokens) if tokens else None, token_attempts=len(tokens), attempts=len(rows),
                raw_format_adjustments=sum(r.get('raw_format_compliant') is False for r in rows),
                platform_failures=sum(r.get('failure_owner') == 'platform' for r in rows),
                unclassified_failures=sum(r.get('failure_owner') == 'unclassified' for r in rows))


def report(run, cfg, case_ids):
    data = summary(run, cfg, case_ids)
    save(run / 'evaluation-summary.json', data)
    rate = data['first_success_rate']
    lines = ['## 执行稳定性', '',
             '测评模式：' + MODES.get(data['mode'], '历史批次未记录') + '。',
             '首次完整答卷率按已结束且有记录的首次执行统计，剔除已识别平台故障和主动中断；归因未确认的失败不能直接解释为 Agent 故障率。',
             f"首次完成：{data['first_succeeded']}/{data['first_observed']}（" + (f'{rate:.1%}' if rate is not None else '未知') + '）。',
             f"首次结果覆盖：{data['first_observed']}/{data['total']}；最终答卷：{data['final_complete']}/{data['total']}。",
             f"后续执行或恢复查询：{data['platform_followups']} 次；详细记录见 evaluation-summary.json。",
             f"首次已记录耗时：{data['first_elapsed_seconds']} 秒；首次已知 Token：{data['first_known_tokens']}。",
             f"平台累计执行/查询耗时（不等于服务端检视耗时）：{data['elapsed_seconds']} 秒；已知 Token：{data['known_tokens']}（有记录尝试 {data['token_attempts']}/{data['attempts']}）。",
             f"已识别平台故障：{data['platform_failures']} 次；未确认归因失败：{data['unclassified_failures']} 次。",
             '恢复查询不再次累计远端任务 Token；LCR 续跑用量可能包含前次会话，未确认增量时不累加。',
             '冻结执行条件：`' + json.dumps({key: cfg.get(key) for key in (
                 'timeout_minutes', 'case_jobs', 'concurrency', 'max_case_retries', 'lcr_options',
                 'case_execution_policy', 'dsh_policy')}, ensure_ascii=False, sort_keys=True) + '`',
             '运行条件以冻结配置为准，未验证与线上一致；缺失数据不按零统计，强杀留下的 running 记录不算已完成。', '']
    target = run / 'report.zh-CN.md'
    if target.exists():
        content = target.read_text(encoding='utf-8').split('\n<!-- evaluation-summary -->')[0]
        target.write_text(content + '\n<!-- evaluation-summary -->\n' + '\n'.join(lines), encoding='utf-8')
