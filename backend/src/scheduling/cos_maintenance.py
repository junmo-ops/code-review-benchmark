"""Persistent, fail-closed maintenance barrier; tickets never expire automatically."""
from contextlib import contextmanager
import uuid
from .contracts import decode, encode

KEY = 'cos-maintenance'


def active(db, scope):
    db.execute('SELECT value FROM bench_web_state WHERE scope=%s AND name=%s', (scope, KEY))
    row = db.fetchone()
    return bool(row and decode(row['value']).get('active'))


def ticket(queue, kind):
    name = 'cos-writer-' + uuid.uuid4().hex
    with queue._locked() as (db, _):
        if active(db, queue.scope):
            raise ValueError('COS 正在维护，暂不接受新操作')
        db.execute('INSERT INTO bench_web_state(scope,name,value,revision) VALUES(%s,%s,%s,1)',
                   (queue.scope, name, encode({'kind': kind})))
    return name


def release(queue, name):
    with queue._locked() as (db, _):
        db.execute('DELETE FROM bench_web_state WHERE scope=%s AND name=%s', (queue.scope, name))


@contextmanager
def writer(queue, kind):
    name = ticket(queue, kind)
    try:
        yield
    finally:
        release(queue, name)


def status(queue):
    with queue._locked() as (db, _):
        enabled = active(db, queue.scope)
        db.execute("SELECT COUNT(*) AS n FROM bench_web_state WHERE scope=%s AND name LIKE 'cos-writer-%%'", (queue.scope,))
        writers = db.fetchone()['n']
        db.execute("SELECT COUNT(*) AS n FROM bench_scheduler_tasks WHERE scope=%s AND status IN ('running','cancelling')", (queue.scope,))
        tasks = db.fetchone()['n']
    return {'active': enabled, 'writers': writers, 'tasks': tasks, 'ready': enabled and not writers and not tasks}


def change(queue, enabled):
    with queue._locked() as (db, _):
        db.execute('SELECT name FROM bench_web_state WHERE scope=%s AND name=%s', (queue.scope, 'cos-operation'))
        if db.fetchone():
            raise ValueError('扫描或删除尚未结束，不能切换维护状态')
        db.execute('INSERT INTO bench_web_state(scope,name,value,revision) VALUES(%s,%s,%s,1) '
                   'ON DUPLICATE KEY UPDATE value=VALUES(value),revision=revision+1',
                   (queue.scope, KEY, encode({'active': enabled})))
        db.execute('DELETE FROM bench_web_state WHERE scope=%s AND name=%s', (queue.scope, 'cos-preview'))
    return status(queue)


@contextmanager
def operation(queue):
    if not status(queue)['ready']:
        raise ValueError('请先进入维护状态，并等待在途操作和任务结束')
    with queue._locked() as (db, _):
        if not active(db, queue.scope):
            raise ValueError('维护状态已退出')
        db.execute('SELECT name FROM bench_web_state WHERE scope=%s AND name=%s', (queue.scope, 'cos-operation'))
        if db.fetchone():
            raise ValueError('已有扫描或删除正在执行')
        db.execute('INSERT INTO bench_web_state(scope,name,value,revision) VALUES(%s,%s,%s,1)',
                   (queue.scope, 'cos-operation', '{}'))
    try:
        yield
    finally:
        release(queue, 'cos-operation')


def install(app, dashboard):
    from flask import g, request, jsonify
    @app.before_request
    def cos_barrier():
        if request.method in {'GET', 'HEAD', 'OPTIONS'} or request.path.startswith('/api/cos/') or not request.path.startswith('/api/') or request.path == '/api/session':
            return None
        try:
            g.cos_writer = ticket(dashboard.queue, 'web')
        except ValueError as error:
            return jsonify(error=str(error)), 503
        return None

    @app.teardown_request
    def cos_release(_error):
        name = getattr(g, 'cos_writer', None)
        if name:
            g.cos_writer = None
            release(dashboard.queue, name)
