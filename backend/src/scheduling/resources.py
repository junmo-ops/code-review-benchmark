"""Resource work uses the same queue, attempts and fenced transaction as evaluations."""
from application.files import now
from .contracts import decode, task_identity

RUNTIME = 'resource_v1'
BOOTSTRAP = 'resources_bootstrap_v1'


def ensure_task(queue, db, key, value=None):
    run_id = 'resource_' + value['id'] if value else BOOTSTRAP
    task_id = task_identity(run_id, '__resource__')
    db.execute('SELECT task_id,status,generation FROM bench_scheduler_tasks WHERE scope=%s AND task_id=%s',
               (queue.scope, task_id))
    previous = db.fetchone()
    if not previous:
        payload = dict(stage='resource', run_id=run_id, runtime_key=RUNTIME, key=key, upload=value)
        queue._insert_task(db, run_id, '__resource__', payload, 'shared_gateway', 3)
    elif key == 'bootstrap' and previous['status'] in {'failed', 'interrupted'}:
        # A new Web startup after configuration repair may retry initialization.
        # Keep all old attempts; never reopen a case or user import here.
        db.execute("UPDATE bench_scheduler_tasks SET status='queued',result=NULL,owner=NULL,token=NULL,"
                   'lease_until=NULL,max_attempts=generation+3,updated_at=UTC_TIMESTAMP(6) '
                   'WHERE scope=%s AND task_id=%s', (queue.scope, task_id))


def bootstrap(queue):
    with queue._locked() as (db, _):
        db.execute("SELECT name FROM bench_web_state WHERE scope=%s AND name IN ('resources','models')",
                   (queue.scope,))
        if len(db.fetchall()) < 2:
            ensure_task(queue, db, 'bootstrap')


def submit(queue, state, key, value, revision):
    with queue._locked() as (db, _):
        state._put(db, key, value, revision)
        ensure_task(queue, db, key, value)


def reconcile(queue, db):
    """Also adopt unfinished imports from the former Web executor after it is stopped."""
    from .web_state import WebState
    state = WebState(queue)
    db.execute("SELECT * FROM bench_web_state WHERE scope=%s AND name LIKE 'upload-%%' "
               "AND JSON_UNQUOTE(JSON_EXTRACT(value,'$.status')) IN ('queued','running')", (queue.scope,))
    for row in db.fetchall():
        value = decode(row['value'])
        if value['status'] not in {'queued', 'running'}:
            continue
        ensure_task(queue, db, row['name'], value)
        task_id = task_identity('resource_' + value['id'], '__resource__')
        db.execute('SELECT status,generation,checkpoint FROM bench_scheduler_tasks WHERE scope=%s AND task_id=%s',
                   (queue.scope, task_id))
        task = db.fetchone()
        changes = dict(status=task['status'], attempts=task['generation'])
        changes.update((decode(task['checkpoint']) or {}).get('progress', {}))
        changes.pop('updated_at', None)
        if task['status'] == 'interrupted':
            changes.update(status='failed', message='资源处理连续中断三次，请检查 Worker 日志后重新上传', ended_at=now())
        if any(value.get(key) != item for key, item in changes.items()):
            changes['updated_at'] = now()
            state._put(db, row['name'], dict(value, **changes), row['revision'])


def publish(queue, db, lease):
    """Called inside finish's lease-checked transaction; no COS I/O here."""
    from .web_state import WebState
    state = WebState(queue)
    db.execute('SELECT status,result FROM bench_scheduler_tasks WHERE scope=%s AND task_id=%s',
               (queue.scope, lease.task_id))
    task = db.fetchone()
    record = decode(task['result'])
    if lease.payload['key'] == 'bootstrap':
        if task['status'] == 'succeeded':
            _publish_seed(queue, db, state, record['catalogs'])
        return
    key = lease.payload['key']
    db.execute('SELECT * FROM bench_web_state WHERE scope=%s AND name=%s', (queue.scope, key))
    row = db.fetchone()
    value = decode(row['value'])
    if task['status'] == 'succeeded':
        catalog = record.get('catalog')
        if catalog:
            state._put(db, 'resources', catalog['value'], catalog['revision'])
        changes = dict(record['changes'], chunks=[])
    else:
        changes = dict(status='failed', message=record.get('error', '资源处理失败，请查看 Worker 日志'))
    changes.update(attempts=lease.generation, updated_at=now(), ended_at=now())
    state._put(db, key, dict(value, **changes), row['revision'])


def _publish_seed(queue, db, state, catalogs):
    for key, record in catalogs.items():
        db.execute('SELECT name FROM bench_web_state WHERE scope=%s AND name=%s', (queue.scope, key))
        if not db.fetchone():
            state._put(db, key, {'snapshot': record}, 0)
