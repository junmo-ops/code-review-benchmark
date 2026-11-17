"""Shared Web records. Short SQL transactions publish immutable COS pointers."""
import time
from .contracts import decode, encode


class WebState:
    def __init__(self, queue):
        self.queue = queue

    def get(self, key):
        with self.queue.database.transaction() as db:
            db.execute('SELECT * FROM bench_web_state WHERE scope=%s AND name=%s',
                       (self.queue.scope, key))
            return self._decode(db.fetchone())

    @staticmethod
    def _decode(row):
        if row:
            row['value'] = decode(row['value'])
        return row

    def listing(self, prefix):
        with self.queue.database.transaction() as db:
            db.execute('SELECT * FROM bench_web_state WHERE scope=%s AND name LIKE %s ORDER BY name',
                       (self.queue.scope, prefix + '%'))
            return [self._decode(row) for row in db.fetchall()]

    def put(self, key, value, revision=0):
        with self.queue._locked() as (db, _):
            self._put(db, key, value, revision)
        return self.get(key)

    def _put(self, db, key, value, revision):
        if revision == 0:
            db.execute('SELECT revision FROM bench_web_state WHERE scope=%s AND name=%s',
                       (self.queue.scope, key))
            if db.fetchone():
                raise ValueError('共享状态已更新，请刷新后重试')
            db.execute('INSERT INTO bench_web_state(scope,name,value,revision) VALUES(%s,%s,%s,1)',
                       (self.queue.scope, key, encode(value)))
        else:
            db.execute('UPDATE bench_web_state SET value=%s,revision=revision+1 '
                       'WHERE scope=%s AND name=%s AND revision=%s',
                       (encode(value), self.queue.scope, key, revision))
            if db.rowcount != 1:
                raise ValueError('共享状态已更新，请刷新后重试')

    def once(self, key, value):
        with self.queue._locked() as (db, _):
            db.execute('SELECT * FROM bench_web_state WHERE scope=%s AND name=%s',
                       (self.queue.scope, key))
            row = self._decode(db.fetchone())
            if row:
                return row
            self._put(db, key, value, 0)
        return self.get(key)

    def admit(self, key, value):
        with self.queue._locked() as (db, _):
            db.execute("SELECT value FROM bench_web_state WHERE scope=%s AND name LIKE 'upload-%%'",
                       (self.queue.scope,))
            rows = [decode(row['value']) for row in db.fetchall()]
            active = [row for row in rows if row.get('status') in {'queued', 'running'}
                      or (row.get('status') == 'receiving' and row['expires_at'] > time.time())]
            resumed = self._resumable(active, value)
            if resumed:
                return resumed
            if value['kind'] != 'zip' and any(row['kind'] != 'zip' for row in active):
                raise ValueError('已有资源上传或导入，请先完成或放弃当前任务')
            self._zip_capacity(rows, active, value['kind'])
            self._put(db, key, value, 0)
        return value

    @staticmethod
    def _zip_capacity(rows, active, kind):
        if kind != 'zip':
            return
        retained = [row for row in rows if row['kind'] == 'zip' and not row.get('deleted')
                    and (row['status'] in {'queued', 'running'} or row['expires_at'] > time.time())]
        if len(retained) >= 30 or sum(row['kind'] == 'zip' for row in active) >= 5:
            raise ValueError('最多保留 30 条记录、同时处理 5 个 ZIP，请先下载并清理')

    @staticmethod
    def _resumable(rows, value):
        for row in rows:
            if (row['status'] == 'receiving' and all(row.get(field) == value.get(field)
                    for field in ('kind', 'fingerprint', 'main', 'filename', 'size'))):
                return row
        return None
