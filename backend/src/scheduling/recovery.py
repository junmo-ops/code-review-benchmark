"""Explicit original-task queries, with immutable evidence and fenced requeueing."""
import hashlib
from pathlib import Path
import tempfile
from application.files import read
from .contracts import decode, encode

RETRYABLE = {'failed', 'interrupted', 'cancelled'}
FINISHED = {'succeeded', 'partial', 'failed', 'cancelled'}


def evidence(task):
    for key in ('result', 'checkpoint'):
        record = decode(task.get(key)) or {}
        if record.get('snapshot'):
            return record
    return None


def available(task, reviewer):
    return reviewer == 'server_agent' and task['status'] in RETRYABLE and evidence(task) is not None


def inspect(store, task, safe_id):
    """Read only the remote receipt, outside the SQL transaction; never send a POST."""
    from .artifacts import safe_path
    record = evidence(task)
    if not record:
        raise ValueError('没有已保存的案例证据，不能恢复原任务查询')
    receipt = safe_path(f'raw/{safe_id}.remote.json').as_posix()
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / 'manifest.json'
        store.get(record['snapshot'], path)
        files = read(path)
        if not isinstance(files, dict) or receipt not in files:
            raise ValueError('没有原服务任务记录，禁止恢复时重新创建任务')
        store.get(files[receipt], path)
        remote = read(path)
    task_id = remote.get('task_id') if isinstance(remote, dict) else None
    if not isinstance(task_id, str) or not task_id or len(task_id) > 200:
        raise ValueError('原服务任务 ID 未确认，不能自动恢复或重新创建')
    return dict(generation=task['generation'], snapshot=record['snapshot'], task_id=task_id)


def requeue(queue, db, run_id, job, case_id, expected):
    db.execute('SELECT * FROM bench_scheduler_runs WHERE scope=%s AND run_id=%s', (queue.scope, run_id))
    run = db.fetchone()
    if not run or run['status'] not in FINISHED:
        raise ValueError('批次正在执行，不能重复恢复查询')
    plan = decode(run['plan'])
    task = next((row for row in queue._rows(db, run_id) if row['case_id'] == case_id), None)
    if not task or not available(task, plan['spec']['reviewer']):
        raise ValueError('仅支持恢复有原任务证据的未完成服务端 Agent 案例')
    record = evidence(task)
    if task['generation'] != expected['generation'] or record['snapshot'] != expected['snapshot']:
        raise ValueError('案例证据已更新，请刷新后重新操作')
    payload = dict(decode(task['payload']), query_only=True, remote_task_id=expected['task_id'])
    text = encode(payload)
    attempts = 1 if plan['spec']['evaluation_mode'] == 'stability' else 3
    db.execute("UPDATE bench_scheduler_tasks SET status='queued',payload=%s,payload_hash=%s,checkpoint=%s,"
               'result=NULL,owner=NULL,token=NULL,lease_until=NULL,max_attempts=%s,updated_at=UTC_TIMESTAMP(6) '
               'WHERE scope=%s AND task_id=%s',
               (text, hashlib.sha256(text.encode()).hexdigest(), encode(record), task['generation'] + attempts,
                queue.scope, task['task_id']))
    # Keep generations and attempt rows; retain the old aggregate and its published scores.
    db.execute("UPDATE bench_scheduler_runs SET status='reviewing',aggregate_id=NULL,job=%s,"
               'updated_at=UTC_TIMESTAMP(6) WHERE scope=%s AND run_id=%s', (encode(job), queue.scope, run_id))


def finished_status(aggregate_status, cases, summary=None):
    if aggregate_status != 'succeeded':
        return 'failed'
    statuses = [row['status'] for row in cases]
    if not statuses or all(status == 'succeeded' for status in statuses):
        return 'partial' if (summary or {}).get('status') == 'partial_scored' else 'succeeded'
    return 'partial' if 'succeeded' in statuses else 'failed'
