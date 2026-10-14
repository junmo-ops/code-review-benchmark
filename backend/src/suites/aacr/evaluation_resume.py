"""Preserve AACR scope, answers and completed rounds when continuing a failed eval."""
import asyncio
import hashlib
import json
import tempfile
from pathlib import Path

from runner.storage import save, validate_answer
from reviewers.lcr.adapter import answer_session_failures
from .judge_transport import REQUEST_BUDGET


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def complete_answers(all_instances, run):
    included, excluded = [], []
    for item in all_instances:
        try:
            answer = validate_answer(item, run / 'results' / (item.safe_id + '.json'))
            if answer_session_failures(item, run, answer):
                raise ValueError('LCR 会话记录了失败条目')
        except (OSError, ValueError) as error:
            excluded.append(dict(instance_id=item.instance_id, reason='缺少完整答卷' if isinstance(error, FileNotFoundError) else '答卷未通过完整性或会话校验'))
        else:
            included.append(item)
    return included, excluded


def prepare_scope(all_instances, run):
    path = run / 'evaluation-scope.json'
    if path.exists():
        scope = read(path)
        ids = scope['included']
        selected = {item.instance_id: item for item in all_instances}
        if len(ids) != len(set(ids)) or any(key not in selected for key in ids):
            raise ValueError('已有评分范围与批次案例清单不一致')
        included, excluded = complete_answers([selected[key] for key in ids], run)
        if excluded:
            raise ValueError('原评分范围中的答卷不再完整，拒绝继续评分')
        return included
    if (run / 'metrics').exists() or (run / 'judge-requests.json').exists():
        raise ValueError('已有评分证据但缺少评分范围，拒绝覆盖')
    included, excluded = complete_answers(all_instances, run)
    if not included:
        raise ValueError('没有可评分的完整答卷；请先完成至少一个案例。')
    save(path, dict(included=[item.instance_id for item in included], excluded=excluded,
                    note=f'本轮共 {len(all_instances)} 例，其中 {len(included)} 例纳入评分；未完成案例不计零分。'))
    return included


def prepare_state(instances, run, config):
    expected = dict(suite_id='aacr', suite_identity=config.get('suite', {}).get('identity'),
                    judge_model=config['judge_model'], judge_endpoint_sha256=config['judge_endpoint_sha256'],
                    eval_rounds=config.get('eval_rounds', 3), line_k=config.get('line_k', 1))
    path = run / 'evaluation-config.json'
    if path.exists() and read(path) != expected:
        raise ValueError('评分配置已变化，拒绝复用旧评分')
    if (run / 'metrics').exists() and not path.exists():
        raise ValueError('已有评分目录缺少冻结评分配置，拒绝覆盖')
    audit = run / 'judge-audit.json'
    if audit.exists() and read(audit).get('status') == 'complete':
        raise ValueError('评分已完成，不覆盖已有评分；生成报告请使用报告按钮')
    manifest = dict(dataset_sha256=sha(run / 'dataset.jsonl'),
                    answers={item.instance_id: sha(run / 'results' / (item.safe_id + '.json')) for item in instances},
                    config=expected)
    checkpoint = run / 'evaluation-resume.json'
    if checkpoint.exists() and read(checkpoint) != manifest:
        raise ValueError('评分答卷或数据已变化，拒绝复用旧评分')
    save(path, expected)
    save(checkpoint, manifest)


def round_result(run, index, instances):
    paths = list((run / 'metrics').glob(f'metrics_ocr_*_round_{index}.json'))
    if len(paths) > 1:
        raise ValueError(f'第 {index} 轮存在多份评分，拒绝猜测使用哪一份')
    if not paths:
        return None
    result = read(paths[0])
    ids = [row['instance_id'] for row in result['eval_res']]
    summary = result['summary']
    if ids != [item.instance_id for item in instances] or summary.get('missing_instances') != 0:
        raise ValueError(f'第 {index} 轮保存结果不完整或案例范围不一致')
    return result


def run_round(instances, run, config, transport, index, previous):
    import evaluate
    transport.begin_round(index, completed=previous is not None)
    mode = '校验并复用已完成评分（不调用模型）' if previous else '继续评分（成功请求复用缓存）'
    print(f'--- 第 {index}/{config.get("eval_rounds", 3)} 轮：{mode} ---', flush=True)
    # A completed file stays byte-for-byte unchanged, including its timestamp.
    with tempfile.TemporaryDirectory(prefix='aacr-eval-') as folder:
        target = Path(folder) if previous else run / 'metrics'
        result = asyncio.run(evaluate.evaluate(instances=instances, results_dir=run / 'results',
                            reviewer='ocr', output_dir=target, line_k=config.get('line_k', 1),
                            limit=None, round_label=f'_round_{index}'))
    if previous is not None and (result['summary'] != previous['summary'] or result['eval_res'] != previous['eval_res']):
        raise ValueError(f'第 {index} 轮复验与已保存评分不同，已保留原文件并停止')
    return previous if previous is not None else result


def evaluate_rounds(instances, run, config, transport):
    import pipeline
    rounds = config.get('eval_rounds', 3)
    previous = [round_result(run, i, instances) for i in range(1, rounds + 1)]
    results = [run_round(instances, run, config, transport, i, saved)
               for i, saved in enumerate(previous, 1)]
    average = dict(summary=pipeline._average_summaries([r['summary'] for r in results]), eval_rounds=rounds)
    path = run / 'metrics/metrics_ocr_average.json'
    if path.exists() and read(path) != average:
        raise ValueError('已有平均评分与本次续评不一致，拒绝覆盖')
    if not path.exists():
        save(path, average)
    pipeline._print_average_summary('ocr', average['summary'], rounds)


def scoring_budget(run, config):
    """Bound all potential comparisons, retries and report work instead of 15 min."""
    from vendor.aacr.evaluation.schema import load_instances
    scope = run / 'evaluation-scope.json'
    selected = set(read(scope)['included']) if scope.exists() else None
    comparisons = 0
    for item in load_instances(run / 'dataset.jsonl'):
        if selected is not None and item.instance_id not in selected:
            continue
        path = run / 'results' / (item.safe_id + '.json')
        if not path.exists():
            continue
        answer = read(path)
        comments = answer.get('review', {}).get('comments', [])
        comparisons += len(item.reference_comments) * len(comments)
    return max(3600, comparisons * config.get('eval_rounds', 3) * REQUEST_BUDGET + 900)
