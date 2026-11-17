"""Single-host Agent case concurrency under the existing whole-batch lock."""
import json
import os
import subprocess
import sys
import time
from runner.storage import save


TERMINAL = frozenset({'succeeded', 'failed', 'partial', 'cancelled'})
ATTENTION = 'agent-batch-attention.json'


def remote(run, item):
    path = run / 'raw' / (item.safe_id + '.remote.json')
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {'state': 'unknown'}
    except (OSError, ValueError):
        return {'state': 'unknown'}


def pending_items(items, snapshot, run):
    from . import adapter
    states = {row['instance_id']: row['status'] for row in snapshot}
    pending, failures = [], []
    for item in items:
        if states[item.instance_id] == 'complete':
            adapter.validate_answer(item, run / 'results' / (item.safe_id + '.json'))
        elif remote(run, item).get('state') in TERMINAL:
            failures.append(item.instance_id)
        elif remote(run, item) or states[item.instance_id] != 'pending':
            save(run / 'raw' / ATTENTION, dict(case=item.instance_id, task_id=remote(run, item).get('task_id')))
            raise ValueError('案例已有未完成记录，请先恢复查询原任务：' + item.instance_id)
        else:
            pending.append(item)
    return pending, failures


def launch(item, run, seconds):
    path = run / 'raw' / (item.safe_id + '.platform.log')
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-m', 'reviewers.server_agent.case_command', 'review',
               '--run-id', run.name, '--runs-root', str(run.parent),
               '--instance', item.instance_id, '--external-timeout-seconds', str(seconds)]
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    with path.open('a', encoding='utf-8') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, **options)
    print(f'开始案例：{item.instance_id}；独立日志：{path}', flush=True)
    return dict(item=item, process=process, deadline=time.monotonic() + seconds,
                log_path=path, log_offset=0, next_log=0)


def forward_log(row):
    if row['process'].poll() is None and time.monotonic() < row['next_log']:
        return
    with row['log_path'].open(encoding='utf-8', errors='replace') as log:
        log.seek(row['log_offset'])
        content = log.read(65536)
        row['log_offset'] = log.tell()
    for line in content.splitlines():
        print(f"[{row['item'].instance_id}] {line}", flush=True)
    row['next_log'] = time.monotonic() + 1


def collect(active, run, failures):
    blocked = False
    for key, row in list(active.items()):
        process, item = row['process'], row['item']
        forward_log(row)
        timed_out = process.poll() is None and time.monotonic() >= row['deadline']
        if timed_out:
            from local import terminate
            terminate(process)
        if process.poll() is None:
            continue
        del active[key]
        if process.returncode != 0 or timed_out:
            failures.append(item.instance_id)
            known = remote(run, item)
            if known.get('state') not in TERMINAL:
                blocked = True
                save(run / 'raw' / ATTENTION, dict(case=item.instance_id, task_id=known.get('task_id')))
        print(f'案例结束：{item.instance_id}；退出码：{process.returncode}；任务 ID：{remote(run, item).get("task_id", "未知")}', flush=True)
    return blocked


def execute(items, run, jobs, seconds, failures):
    active, offset, blocked = {}, 0, False
    try:
        while active or (offset < len(items) and not blocked):
            blocked = collect(active, run, failures) or blocked
            while not blocked and len(active) < jobs and offset < len(items):
                item = items[offset]
                active[item.safe_id] = launch(item, run, seconds)
                offset += 1
            if active:
                time.sleep(.15)
    except BaseException:
        save(run / 'raw' / ATTENTION, dict(reason='本地批次调度中断，远端任务需核实'))
        raise
    finally:
        from local import terminate
        for row in active.values():
            if row['process'].poll() is None:
                terminate(row['process'])
    if blocked:
        raise ValueError('远端任务状态未确认，已暂停提交新案例；其他已提交案例已等待结束。请恢复查询原任务。')
    if failures:
        raise ValueError(f'{len(failures)} 个案例未形成完整答卷；详情见各案例 platform.log')


def review(args, cfg, items, snapshot, run):
    from runner import cloud
    cloud._check_tool_identity(cfg)
    cloud.require_model(cfg)
    pending, failures = pending_items(items, snapshot, run)
    jobs = cfg.get('case_jobs', 1)
    seconds = cfg['timeout_minutes'] * 60 + 60
    required = ((len(pending) + jobs - 1) // jobs) * (seconds + 1) + 30
    if not args.external_timeout_seconds or args.external_timeout_seconds < required:
        raise ValueError(f'平台命令时限不足：批次检视至少需要 {required} 秒')
    (run / 'raw' / ATTENTION).unlink(missing_ok=True)
    print(f'服务端 Agent 案例并发：{jobs}；待执行：{len(pending)}；不自动重建远端任务。', flush=True)
    try:
        execute(pending, run, jobs, seconds, failures)
    finally:
        cloud.adapter.write_summary(items, run)
        save(run / 'cloud-status.json', cloud.states(items, run))
