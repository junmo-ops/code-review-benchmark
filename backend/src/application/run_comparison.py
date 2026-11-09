"""Read existing batch results without executing reviewers or changing scores."""
import hashlib
import json
import math
from .files import read


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _usage(folder, cases):
    rows = []
    for case in cases:
        path = folder / 'results' / (case['id'].replace('/', '__') + '.json')
        if not path.is_file():
            continue
        answer = read(path)
        summary = answer.get('review', {}).get('summary', {})
        rows.append(dict(seconds=answer.get('duration_seconds'), tokens=summary.get('total_tokens')))
    result = {}
    for field in ('seconds', 'tokens'):
        values = [row[field] for row in rows if _number(row[field])]
        result[field] = sum(values) if values else None
        result[field + '_coverage'] = len(values)
    return result


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _conditions(context, key, cfg, cases):
    folder = context.run_dir(key)
    dataset = folder / 'dataset.jsonl'
    dataset_hash = hashlib.sha256(dataset.read_bytes()).hexdigest() if dataset.is_file() else None
    scope = context._score_scope(key)
    if scope is None and cases and all(case.get('scored') is True for case in cases):
        scope = [case['id'] for case in cases]
    return dict(dataset=dataset_hash,
                scope=_digest(sorted(scope)) if scope is not None else None,
                judge=_digest([cfg.get('judge_model'), cfg.get('judge_endpoint_sha256'), cfg.get('evaluator'), cfg.get('judge_parameters')]),
                execution=_digest({name: cfg.get(name) for name in (
                    'evaluation_mode', 'evaluation_protocol', 'timeout_minutes', 'case_jobs', 'max_case_retries', 'lcr_options',
                    'dsh_policy', 'case_execution_policy', 'skill_mode', 'skill_version', 'tool_hashes')}))


def build(context, keys):
    keys = list(dict.fromkeys(keys))
    if not 2 <= len(keys) <= 6:
        raise ValueError('请选择 2–6 个不同测评批次')
    rows, conditions = [], []
    for key in keys:
        detail = context.queries.detail(key)
        if detail is None:
            raise ValueError('所选批次不存在，请返回列表重新选择')
        cfg = context.config(key)
        conditions.append(_conditions(context, key, cfg, detail['cases']))
        evaluation = detail.get('evaluation', {})
        usage = (_evaluation_usage(evaluation) if cfg.get('evaluation_protocol')
                 else _usage(context.run_dir(key), detail['cases']))
        rows.append(dict(run=detail, usage=usage))
    warnings = _warnings(rows, conditions)
    return dict(items=rows, warnings=warnings)


def _warnings(rows, conditions):
    warnings = ['仅核对已冻结的配置记录；记录缺失的历史批次不能据此确认同等测评条件。']
    for field, label in [('dataset', '案例集内容'), ('scope', '纳入评分的案例范围'),
                         ('judge', '裁判或评分配置'), ('execution', '工具版本、Skill 或执行预算')]:
        values = [condition[field] for condition in conditions]
        if None in values:
            warnings.append(label + '有缺失记录，无法确认一致。')
        elif len(set(values)) > 1:
            warnings.append(label + '不同，请结合差异解读结果。')
    if len({row['run']['suite'] for row in rows}) > 1:
        warnings.append('案例集协议不同，分数不能直接排名。')
    if any(row['run'].get('simulation') for row in rows):
        warnings.append('包含模拟批次，不代表真实模型效果。')
    if any(not row['run'].get('metrics') for row in rows):
        warnings.append('部分批次尚无评分，未完成项不计零分。')
    return warnings


def _evaluation_usage(data):
    cases = data.get('cases', [])
    return dict(seconds=data.get('elapsed_seconds'), tokens=data.get('known_tokens'),
                seconds_coverage=sum(any('elapsed_seconds' in r for r in c['attempts']) for c in cases),
                tokens_coverage=sum(any(r.get('known_tokens') is not None for r in c['attempts']) for c in cases))
