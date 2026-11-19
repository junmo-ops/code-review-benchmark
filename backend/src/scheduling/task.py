"""One task per subprocess. Reviewer module globals never cross case boundaries."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from application.files import read, save
from runner.paths import PROJECT_ROOT
from webui.service import Dashboard
from .config import CLUSTER_CREDENTIALS


def command(run, stage, extra=(), timeout=900):
    _run([sys.executable, '-m', 'runner.cloud', stage, '--run-id', run.name,
          '--runs-root', str(run.parent), *extra], _command_environment(), timeout)


def _run(args, env, timeout):
    from local import terminate
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    process = subprocess.Popen(args, env=env, **options)
    try:
        code = process.wait(timeout=timeout)
    finally:
        # Kill descendants while their parent is still alive, including separate LCR sessions.
        if process.poll() is None:
            terminate(process)
    if code:
        raise subprocess.CalledProcessError(code, args)


def _command_environment():
    env = dict(os.environ)
    for key in CLUSTER_CREDENTIALS:
        env.pop(key, None)
    return env


def initialize(payload, data, run):
    from application.planning import ExecutionPlanner
    binding = payload['spec'].get('repository_binding')
    if binding:
        save(run / 'repository-sources.json', binding)
    if (run / 'run-config.json').is_file():
        return
    app = Dashboard(load_jobs=False)
    spec = dict(payload['spec'], dataset='input/dataset.jsonl')
    # Dataset resolution is local to this process and points to the downloaded immutable input.
    app.dataset_path = lambda _name: data / 'input/dataset.jsonl'
    _install_skills(spec, data)
    plan = ExecutionPlanner(app)
    env = app.environment(payload['profile'])
    cmd, _, limit = plan._agent_command('init', run.name, env, spec, spec, None)
    if spec['reviewer'] == 'lcr':
        cmd += ['--case-jobs', '1', '--max-case-retries', str(spec.get('max_case_retries', 1))]
    env['BENCH_CASE_STORAGE_MODE'] = 'local'
    _run(cmd, env, limit)
    binding = spec.get('repository_binding')
    if binding:
        save(run / 'repository-sources.json', binding)


def prepare_case(payload, data, run):
    from runner.cloud import checked_instances
    from webui.automatic_repositories import prepare_cases, sources
    cfg = read(run / 'run-config.json')
    items = checked_instances(run / 'dataset.jsonl', cfg['suite']['id'])
    item = next(row for row in items if row.instance_id == payload['case']['id'])
    if cfg['reviewer'] == 'server_agent' and cfg.get('diff_mode') == 'direct':
        return item
    path = run / 'repository-sources.json'
    references = read(path)['repositories'] if path.exists() else sources(items)
    prepare_cases(data, [item], references, lambda _stage, message, *_args: print(message, flush=True))
    return item


def review(payload, data, run):
    item = prepare_case(payload, data, run)
    cfg = read(run / 'run-config.json')
    raw = run / 'raw'
    remote = raw / (item.safe_id + '.remote.json')
    resume = cfg['reviewer'] == 'server_agent' and remote.exists()
    if payload.get('query_only'):
        if not resume or read(remote).get('task_id') != payload.get('remote_task_id'):
            raise ValueError('原服务任务记录缺失或 ID 不一致；禁止改为新建检视')
    if resume and not read(remote).get('task_id'):
        raise ValueError('远程提交状态不确定，禁止重复创建服务任务；请核对服务端任务')
    if cfg['reviewer'] == 'lcr':
        from runner import cloud
        from reviewers.registry import get_reviewer
        adapter = get_reviewer('lcr')
        adapter.configure(cfg, run)
        adapter.REPO = data / 'repos' / item.repo.replace('/', '__')
        limit = adapter.case_execution_timeout(item) + 90
    else:
        limit = cfg['timeout_minutes'] * 60 + 90
    extra = ['--instance', item.instance_id]
    if not resume:
        extra += ['--external-timeout-seconds', str(limit)]
        # A fresh attempt workspace has no live processes. Only explicit platform takeover uses retry.
        if payload.get('takeover') and cfg['reviewer'] == 'lcr':
            extra += ['--retry']
    command(run, 'recover' if resume else 'review', extra, timeout=limit + 30)


def aggregate(payload, run):
    cfg = read(run / 'run-config.json')
    action = payload.get('action', 'eval')
    if action == 'eval' and not cfg.get('simulation'):
        from application.planning import ExecutionPlanner
        command(run, 'eval', timeout=ExecutionPlanner._scoring_budget(cfg, run) + 60)
    if not cfg.get('simulation'):
        command(run, 'report')
    _cluster_report(payload, run)
    command(run, 'export')


def rescore(run):
    from application.planning import ExecutionPlanner
    config = read(run / 'run-config.json')
    timeout = ExecutionPlanner._scoring_budget(config, run) + 960
    _run([sys.executable, '-m', 'scheduling.rescore', str(run)], _command_environment(), timeout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('request')
    args = parser.parse_args()
    payload = read(Path(args.request))
    from runner.runtime import data_root
    data = data_root(PROJECT_ROOT)
    run = data / 'runs' / payload['run_id']
    run.mkdir(parents=True, exist_ok=True)
    if payload['stage'] == 'prepare':
        initialize(payload, data, run)
    elif payload['stage'] == 'case':
        review(payload, data, run)
    elif payload['stage'] == 'rescore':
        rescore(run)
    else:
        aggregate(payload, run)
    # Query the actual files with the existing presentation/scoring logic.
    app = Dashboard(load_jobs=False)
    summary = app.describe(payload['run_id'])
    save(data / 'task-summary.json', summary)
    if payload['stage'] == 'case':
        selected = next(case for case in summary['cases'] if case['id'] == payload['case']['id'])
        if selected['status'] not in {'complete', 'simulation_complete'}:
            raise ValueError('检视未形成完整答卷：' + selected['status'])


def _cluster_report(payload, run):
    rows = payload.get('cases', [])
    lines = ['# 集群执行记录', '',
             '任务接手不等于首次执行成功；以下尝试包括实例中断，未回传用量不能按零计。', '',
             '| 案例 | 调度状态 | 领取次数 |', '| --- | --- | --- |']
    for row in rows:
        lines.append(f"| {row['case_id']} | {row['status']} | {row['generation']} |")
    lines += ['', '各次尝试及 COS 证据摘要见 cluster-attempts.json。',
              '已发布的评分仍沿用原评分口径；实例丢失期间未记录的耗时与 Token 不做推算。']
    (run / 'cluster-execution.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _install_skills(spec, data):
    import shutil
    for version in spec.get('skill_versions', []):
        shutil.copytree(data / 'input/skills' / version, data / 'skill-assets/versions' / version)


if __name__ == '__main__':
    main()
