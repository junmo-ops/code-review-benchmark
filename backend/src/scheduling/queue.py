"""MySQL case queue with scoped admission limits and fenced result publication.

Every mutation locks the scope row first. The brief serialized admission section
avoids count/claim races without relying on SKIP LOCKED support in a proxy.
Execution and artifact I/O must happen outside these transactions.
"""
from contextlib import contextmanager
import hashlib
import uuid
from .contracts import Lease, LeaseLost, decode, encode, identifier, positive, task_identity


class CaseQueue:
    def __init__(self, database, scope):
        self.database = database
        self.scope = identifier(scope, 64)

    @contextmanager
    def _locked(self):
        with self.database.transaction() as db:
            db.execute('SELECT * FROM bench_scheduler_scopes WHERE scope=%s FOR UPDATE', (self.scope,))
            settings = db.fetchone()
            if not settings:
                raise ValueError('调度环境尚未初始化')
            yield db, settings

    def bootstrap(self, concurrency=None):
        """Web-only startup: initialize DML atomically, preserving pause and existing limits."""
        if concurrency is not None:
            positive(concurrency)
        with self.database.transaction() as db:
            # Upsert acquires the same scope lock as claim; never unpause on restart.
            db.execute('INSERT INTO bench_scheduler_scopes(scope,global_limit,worker_limit,paused) '
                       'VALUES(%s,5,5,0) ON DUPLICATE KEY UPDATE scope=VALUES(scope)', (self.scope,))
            db.execute('SELECT * FROM bench_scheduler_scopes WHERE scope=%s FOR UPDATE', (self.scope,))
            previous = db.fetchone()
            db.execute('SELECT MIN(concurrency_limit) AS minimum FROM bench_scheduler_pools WHERE scope=%s',
                       (self.scope,))
            minimum = db.fetchone()['minimum']
            # Upgrade from the old multi-limit config must not increase shared model pressure.
            inherited = min(previous['global_limit'], minimum or previous['global_limit'])
            limit = concurrency if concurrency is not None else inherited
            db.execute('UPDATE bench_scheduler_scopes SET global_limit=%s,worker_limit=%s WHERE scope=%s',
                       (limit, min(5, limit), self.scope))
            # Keep old pool IDs for frozen batches, but admission now has one shared ceiling.
            db.execute('UPDATE bench_scheduler_pools SET concurrency_limit=%s WHERE scope=%s', (limit, self.scope))
            db.execute('INSERT INTO bench_scheduler_pools VALUES(%s,%s,%s) ON DUPLICATE KEY UPDATE '
                       'concurrency_limit=VALUES(concurrency_limit)', (self.scope, 'shared_gateway', limit))
        return limit

    def configure(self, global_limit, worker_limit=5, *, paused=False):
        positive(global_limit)
        positive(worker_limit)
        if type(paused) is not bool:
            raise ValueError('paused 必须为布尔值')
        with self.database.transaction() as db:
            db.execute('INSERT INTO bench_scheduler_scopes(scope,global_limit,worker_limit,paused) '
                       'VALUES(%s,%s,%s,%s) ON DUPLICATE KEY UPDATE '
                       'global_limit=VALUES(global_limit),worker_limit=VALUES(worker_limit),paused=VALUES(paused)',
                       (self.scope, global_limit, worker_limit, paused))

    def configure_pool(self, pool, concurrency_limit):
        identifier(pool)
        positive(concurrency_limit)
        with self._locked() as (db, _):
            db.execute('INSERT INTO bench_scheduler_pools VALUES(%s,%s,%s) ON DUPLICATE KEY UPDATE '
                       'concurrency_limit=VALUES(concurrency_limit)', (self.scope, pool, concurrency_limit))

    def enqueue(self, run_id, case_id, payload, *, pool, max_attempts=3):
        task_id = task_identity(run_id, case_id)
        identifier(pool)
        positive(max_attempts)
        if not isinstance(payload, dict):
            raise ValueError('案例执行描述必须是对象')
        text = encode(payload)
        digest = hashlib.sha256(text.encode()).hexdigest()
        with self._locked() as (db, _):
            db.execute('SELECT payload_hash,pool,max_attempts FROM bench_scheduler_tasks '
                       'WHERE scope=%s AND task_id=%s', (self.scope, task_id))
            previous = db.fetchone()
            if previous:
                if previous != dict(payload_hash=digest, pool=pool, max_attempts=max_attempts):
                    raise ValueError('案例已登记，不能更改冻结配置或自动重开终态任务')
                return task_id
            db.execute('SELECT pool FROM bench_scheduler_pools WHERE scope=%s AND pool=%s', (self.scope, pool))
            if not db.fetchone():
                raise ValueError('请先配置模型/网关并发池')
            db.execute('INSERT INTO bench_scheduler_tasks '
                       '(scope,task_id,run_id,case_id,pool,payload,payload_hash,max_attempts) '
                       'VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                       (self.scope, task_id, run_id, case_id, pool, text, digest, max_attempts))
        return task_id

    def _expire(self, db):
        db.execute("UPDATE bench_scheduler_attempts a JOIN bench_scheduler_tasks t "
                   'ON a.scope=t.scope AND a.task_id=t.task_id AND a.generation=t.generation '
                   "SET a.status='expired',a.ended_at=UTC_TIMESTAMP(6) "
                   "WHERE t.scope=%s AND t.status IN ('running','cancelling') "
                   'AND t.lease_until<=UTC_TIMESTAMP(6)', (self.scope,))
        db.execute("UPDATE bench_scheduler_tasks SET status=CASE WHEN status='cancelling' THEN 'cancelled' "
                   "WHEN generation>=max_attempts THEN 'interrupted' ELSE 'queued' END, "
                   'owner=NULL,token=NULL,lease_until=NULL,updated_at=UTC_TIMESTAMP(6) '
                   "WHERE scope=%s AND status IN ('running','cancelling') AND lease_until<=UTC_TIMESTAMP(6)",
                   (self.scope,))

    def reap(self):
        with self._locked() as (db, _):
            self._expire(db)

    def claim(self, owner, lease_seconds=90, runtimes=None):
        identifier(owner)
        positive(lease_seconds)
        with self._locked() as (db, settings):
            self._expire(db)
            from .cos_maintenance import active
            if settings['paused'] or active(db, self.scope) or not self._capacity(db, settings, owner):
                return None
            clause, parameters = _runtime_filter(runtimes)
            db.execute('SELECT t.* FROM bench_scheduler_tasks t JOIN bench_scheduler_pools p '
                       'ON p.scope=t.scope AND p.pool=t.pool '
                       "WHERE t.scope=%s AND t.status='queued' AND "
                       '(SELECT COUNT(*) FROM bench_scheduler_tasks a WHERE a.scope=t.scope AND a.pool=t.pool '
                       "AND a.status IN ('running','cancelling'))<p.concurrency_limit "
                       + clause + ' ORDER BY t.created_at,t.task_id LIMIT 1', (self.scope, *parameters))
            row = db.fetchone()
            return self._claim_row(db, row, owner, lease_seconds) if row else None

    def _capacity(self, db, settings, owner):
        db.execute('SELECT COUNT(*) AS total,COALESCE(SUM(owner=%s),0) AS owned '
                   "FROM bench_scheduler_tasks WHERE scope=%s AND status IN ('running','cancelling')",
                   (owner, self.scope))
        counts = db.fetchone()
        return counts['total'] < settings['global_limit'] and counts['owned'] < settings['worker_limit']

    def _claim_row(self, db, row, owner, seconds):
        token = uuid.uuid4().hex
        generation = row['generation'] + 1
        db.execute("UPDATE bench_scheduler_tasks SET status='running',owner=%s,token=%s,generation=%s,"
                   'lease_until=TIMESTAMPADD(SECOND,%s,UTC_TIMESTAMP(6)),updated_at=UTC_TIMESTAMP(6) '
                   'WHERE scope=%s AND task_id=%s', (owner, token, generation, seconds, self.scope, row['task_id']))
        db.execute('INSERT INTO bench_scheduler_attempts(scope,task_id,generation,owner,token,status) '
                   "VALUES(%s,%s,%s,%s,%s,'running')", (self.scope, row['task_id'], generation, owner, token))
        return Lease(self.scope, row['task_id'], owner, token, generation,
                     decode(row['payload']), decode(row['checkpoint']))

    def _require_lease(self, db, lease):
        if lease.scope != self.scope:
            raise LeaseLost('租约不属于当前环境')
        db.execute('SELECT status FROM bench_scheduler_tasks WHERE scope=%s AND task_id=%s '
                   'AND owner=%s AND token=%s AND generation=%s AND lease_until>UTC_TIMESTAMP(6)',
                   (self.scope, lease.task_id, lease.owner, lease.token, lease.generation))
        row = db.fetchone()
        if not row or row['status'] != 'running':
            raise LeaseLost('租约已失效或任务已取消；停止执行并禁止发布结果')

    def heartbeat(self, lease, lease_seconds=90):
        positive(lease_seconds)
        with self._locked() as (db, _):
            self._require_lease(db, lease)
            db.execute('UPDATE bench_scheduler_tasks SET lease_until=TIMESTAMPADD(SECOND,%s,UTC_TIMESTAMP(6)), '
                       'updated_at=UTC_TIMESTAMP(6) WHERE scope=%s AND task_id=%s',
                       (lease_seconds, self.scope, lease.task_id))

    def checkpoint(self, lease, record):
        text = encode(record)
        with self._locked() as (db, _):
            self._require_lease(db, lease)
            db.execute('UPDATE bench_scheduler_tasks SET checkpoint=%s,updated_at=UTC_TIMESTAMP(6) '
                       'WHERE scope=%s AND task_id=%s', (text, self.scope, lease.task_id))
            db.execute('UPDATE bench_scheduler_attempts SET checkpoint=%s '
                       'WHERE scope=%s AND task_id=%s AND generation=%s',
                       (text, self.scope, lease.task_id, lease.generation))

    def finish(self, lease, record, *, succeeded):
        if type(succeeded) is not bool:
            raise ValueError('必须明确本次执行是否成功')
        text = encode(record)
        status = 'succeeded' if succeeded else 'failed'
        with self._locked() as (db, _):
            self._require_lease(db, lease)
            db.execute('UPDATE bench_scheduler_tasks SET status=%s,result=%s,lease_until=NULL, '
                       'updated_at=UTC_TIMESTAMP(6) WHERE scope=%s AND task_id=%s',
                       (status, text, self.scope, lease.task_id))
            db.execute('UPDATE bench_scheduler_attempts SET status=%s,result=%s,ended_at=UTC_TIMESTAMP(6) '
                       'WHERE scope=%s AND task_id=%s AND generation=%s',
                       (status, text, self.scope, lease.task_id, lease.generation))
            self._after_finish(db, lease)

    def _after_finish(self, db, lease):
        # Plain case queues have no workflow transitions.
        pass

    def cancel_run(self, run_id):
        identifier(run_id)
        with self._locked() as (db, _):
            db.execute("UPDATE bench_scheduler_tasks SET status=CASE WHEN status='queued' THEN 'cancelled' "
                       "ELSE 'cancelling' END,updated_at=UTC_TIMESTAMP(6) WHERE scope=%s AND run_id=%s "
                       "AND status IN ('queued','running')", (self.scope, run_id))

    def tasks(self, run_id):
        identifier(run_id)
        with self.database.transaction() as db:
            db.execute('SELECT task_id,case_id,status,owner,generation,lease_until,checkpoint,result '
                       'FROM bench_scheduler_tasks WHERE scope=%s AND run_id=%s ORDER BY created_at,task_id',
                       (self.scope, run_id))
            return [{**row, 'checkpoint': decode(row['checkpoint']), 'result': decode(row['result'])}
                    for row in db.fetchall()]


def _runtime_filter(runtimes):
    if runtimes is None:
        return '', ()
    if not runtimes:
        return ' AND 1=0', ()
    placeholders = ','.join(['%s'] * len(runtimes))
    return " AND JSON_UNQUOTE(JSON_EXTRACT(t.payload,'$.runtime_key')) IN (" + placeholders + ')', tuple(runtimes)
