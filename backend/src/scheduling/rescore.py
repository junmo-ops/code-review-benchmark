"""Score a batch's saved answers in a new run."""
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

from application.files import now, read, save
from suites.registry import SUITES
from .history import is_history

RUNTIME = 'history_rescore_v1'


def blocked_reason(view):
    if view.get('simulation'):
        return '模拟答卷不参与评分。'
    if view.get('suite') not in SUITES or view.get('evaluator') not in {'original', 'martian'}:
        return '该批次的评分方式暂不支持在多实例中重新评分。'
    if not view.get('completed'):
        return '此批次没有已保存的完整答卷。'
    return None


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _copy_answers(source, target, suite, view):
    from runner.cloud import checked_instances
    from runner.storage import validate_answer
    from reviewers.lcr.adapter import answer_session_failures

    dataset = source / 'dataset.jsonl'
    original = dataset.read_bytes()
    items = checked_instances(dataset, suite.id)
    complete = {case['id'] for case in view['cases'] if case['status'] == 'complete'}
    (target / 'results').mkdir()
    selected = []
    lines = [line for line in original.splitlines(keepends=True) if line.strip()]
    for item, line in zip(items, lines, strict=True):
        if item.instance_id not in complete:
            continue
        filename = item.safe_id + '.json'
        path = source / 'results' / filename
        answer = validate_answer(item, path)
        if answer_session_failures(item, source, answer):
            raise ValueError('答卷存在未处理的检视失败项：' + item.instance_id)
        shutil.copyfile(path, target / 'results' / filename)
        selected.append(line)
    if len(selected) != len(complete) or not selected:
        raise ValueError('批次的完整答卷与案例清单不一致')
    (target / 'dataset.jsonl').write_bytes(original if len(selected) == len(items) else b''.join(selected))
    return _digest(original)


def _prepare(source, target, view, judge, created):
    from runner.cloud import checked_instances
    from scoring.registry import freeze
    original = read(source / 'run-config.json')
    suite = SUITES[view['suite']]
    source_digest = _copy_answers(source, target, suite, view)
    if original.get('dataset_sha256', source_digest) != source_digest:
        raise ValueError('原批次的冻结案例集校验失败')
    dataset = target / 'dataset.jsonl'
    items = checked_instances(dataset, suite.id)
    origin = dict(key=view['key'], total=view['total'], included=len(items), dataset_sha256=source_digest)
    config = dict(original, suite=dict(id=suite.id, identity=suite.identity(), parameters=dict(suite.parameters),
                                       provenance=suite.provenance(dataset)),
                  evaluator=freeze(view['evaluator'], suite), selected_instances=[item.instance_id for item in items],
                  dataset_sha256=_digest(dataset.read_bytes()), scored_from=origin, created_at=created,
                  judge_model=judge['JUDGE_MODEL'], judge_endpoint_sha256=_digest(judge['JUDGE_BASE_URL'].encode()))
    save(target / 'run-config.json', config)
    summary = dict(model=original.get('review_model', view['model']), judge_model=judge['JUDGE_MODEL'],
                   cases=[{**case, 'scored': False} for case in view['cases'] if case['id'] in config['selected_instances']])
    return summary


def _source_view(app, key):
    if not isinstance(key, str):
        raise ValueError('请选择要评分的批次')
    if is_history(key):
        return app.history.detail(key), None, ()
    app.sync()
    row = app.records.get(key)
    if not row:
        return None, None, ()
    tasks = () if row['plan'].get('history_source') else app.queue.tasks(key)
    return app.describe(key, tasks=tasks), row, tasks


def _restore_source(app, view, row, tasks, destination):
    if row is None:
        return app.history.run_dir(view['key'])
    prepared = row['prepared'] or {}
    if not prepared.get('snapshot'):
        raise ValueError('原批次缺少案例集快照')
    answers = {case['id']: 'results/' + (case.get('safe_id') or case['id'].replace('/', '__')) + '.json'
               for case in view['cases'] if case['status'] == 'complete'}
    files = {'run-config.json', 'dataset.jsonl'}
    if row['plan'].get('history_source'):
        files.update(answers.values())
    app.store.restore(prepared['snapshot'], destination, only=files)
    for task in tasks:
        answer = answers.get(task['case_id'])
        if not answer:
            continue
        snapshot = (task['result'] or {}).get('snapshot')
        if not snapshot:
            raise ValueError('找不到案例已发布的答卷：' + task['case_id'])
        app.store.restore(snapshot, destination, only={answer})
    return destination


def submit(app, request):
    from webui.profiles import judge_configuration, save_run_profile

    view, row, tasks = _source_view(app, request.get('key'))
    if not view:
        raise ValueError('找不到原批次')
    reason = blocked_reason(view)
    if reason:
        raise ValueError(reason)
    judge = judge_configuration(app, request.get('judge_profile'))
    created = now()
    name = 'rescore-' + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='saved-answer-scoring-') as folder:
        directory = Path(folder)
        target = directory / 'input'
        target.mkdir()
        source = _restore_source(app, view, row, tasks, directory / 'source')
        summary = _prepare(source, target, view, judge, created)
        snapshot = app.store.capture(target)
    profile = save_run_profile(app, judge)
    job = dict(key=name, id=uuid.uuid4().hex, action='rescore', profile=profile, created_at=created,
               display_name=(view.get('display_name') or view['name']).removesuffix(' · 新评分') + ' · 新评分')
    spec = {key: view[key] for key in ('suite', 'reviewer', 'evaluator')}
    prepared = dict(snapshot=snapshot, summary=summary, runtime_key=RUNTIME)
    app.queue.create_rescore(job, spec, prepared, app.cluster['pool'], view['key'])
    return dict(job_id=job['id'], key=name)


def score(run):
    from runner.cloud import checked_instances
    from runner.evaluation import report
    from scoring.registry import execute
    from suites.registry import resolve_run_suite
    from webui.service import Dashboard
    from .history import package_run

    config = read(run / 'run-config.json')
    origin = config['scored_from']
    dataset = run / 'dataset.jsonl'
    if config['dataset_sha256'] != _digest(dataset.read_bytes()):
        raise ValueError('重评分的冻结案例集已变化')
    if (os.environ.get('JUDGE_MODEL') != config['judge_model']
            or _digest(os.environ.get('JUDGE_BASE_URL', '').encode()) != config['judge_endpoint_sha256']):
        raise ValueError('裁判配置与新批次的冻结配置不同')
    suite = resolve_run_suite(config)
    items = checked_instances(dataset, suite.id)
    dashboard = Dashboard(load_jobs=False)
    if not dashboard.summary(run.name):
        execute('eval', suite, items, run, config)
    if not dashboard.summary(run.name):
        raise ValueError('重评分未生成完整的评分结果')
    target = run / 'report.zh-CN.md'
    if not target.is_file():
        execute('report', suite, items, run, config)
    report(run, config, [item.safe_id for item in items])
    note = f"\n> 来源：`{origin['key']}`。本次使用原批次 {origin['included']}/{origin['total']} 份完整答卷。\n\n"
    original = target.read_text(encoding='utf-8')
    if not original.startswith(note):
        target.write_text(note + original, encoding='utf-8')
    package_run(run)


if __name__ == '__main__':
    score(Path(sys.argv[1]))
