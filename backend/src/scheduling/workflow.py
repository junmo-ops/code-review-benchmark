"""Atomic workflow transitions: prepare -> cases -> one scoring/report task."""
import hashlib
import uuid
from .queue import CaseQueue
from .contracts import decode, encode, identifier, task_identity
from .recovery import finished_status

TERMINAL = {'succeeded', 'failed', 'interrupted', 'cancelled'}


class Workflow(CaseQueue):
    def _insert_task(self, db, run_id, case_id, payload, pool, attempts):
        if 'runtime_key' not in payload:
            payload['runtime_key'] = payload.get('spec', {}).get('cluster_runtime') or payload['prepared']['runtime_key']
        text = encode(payload)
        db.execute('INSERT INTO bench_scheduler_tasks '
                   '(scope,task_id,run_id,case_id,pool,payload,payload_hash,max_attempts) '
                   'VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                   (self.scope, task_identity(run_id, case_id), run_id, case_id, pool, text,
                    hashlib.sha256(text.encode()).hexdigest(), attempts))

    def create(self, job, specification, input_record, cases, pool, profile):
        identifier(job['key'])
        with self._locked() as (db, _):
            db.execute('SELECT run_id FROM bench_scheduler_runs WHERE scope=%s AND run_id=%s',
                       (self.scope, job['key']))
            if db.fetchone():
                raise ValueError('批次名称已存在')
            db.execute('SELECT pool FROM bench_scheduler_pools WHERE scope=%s AND pool=%s', (self.scope, pool))
            if not db.fetchone():
                raise ValueError('请先配置集群并发池')
            plan = dict(spec=specification, input=input_record, cases=cases, pool=pool, profile=profile,
                        auto=job['start_after_create'])
            db.execute('INSERT INTO bench_scheduler_runs(scope,run_id,job,plan,status) '
                       "VALUES(%s,%s,%s,%s,'preparing')", (self.scope, job['key'], encode(job), encode(plan)))
            self._insert_task(db, job['key'], '__prepare__', dict(stage='prepare', run_id=job['key'], **plan), pool, 3)

    def create_rescore(self, job, spec, prepared, pool, source):
        run_id = identifier(job['key'])
        case_id = '__rescore__'
        plan = dict(spec=spec, history_source=source)
        with self._locked() as (db, _):
            db.execute('INSERT INTO bench_scheduler_runs(scope,run_id,job,plan,prepared,aggregate_id,status) '
                       "VALUES(%s,%s,%s,%s,%s,%s,'scoring')",
                       (self.scope, run_id, encode(job), encode(plan), encode(prepared), task_identity(run_id, case_id)))
            payload = dict(stage='rescore', run_id=run_id, prepared=prepared, profile=job['profile'])
            self._insert_task(db, run_id, case_id, payload, pool, 3)

    def _rows(self, db, run_id):
        db.execute('SELECT * FROM bench_scheduler_tasks WHERE scope=%s AND run_id=%s', (self.scope, run_id))
        return db.fetchall()

    def _after_finish(self, db, lease):
        if lease.payload['stage'] == 'resource':
            from .resources import publish
            publish(self, db, lease)
        else:
            self._advance(db, lease.payload['run_id'])

    def reconcile(self):
        with self._locked() as (db, _):
            self._expire(db)
            from .resources import reconcile
            reconcile(self, db)
            db.execute("SELECT run_id FROM bench_scheduler_runs WHERE scope=%s AND status NOT IN "
                       "('succeeded','partial','failed','cancelled','ready')", (self.scope,))
            for row in db.fetchall():
                self._advance(db, row['run_id'])

    def _advance(self, db, run_id):
        db.execute('SELECT * FROM bench_scheduler_runs WHERE scope=%s AND run_id=%s', (self.scope, run_id))
        run = db.fetchone()
        if not run or run['status'] in {'succeeded', 'partial', 'failed', 'ready', 'cancelled'}:
            return
        rows = self._rows(db, run_id)
        if run['status'] == 'cancelling':
            if all(row['status'] in TERMINAL for row in rows):
                self._status(db, run_id, 'cancelled')
            return
        plan = decode(run['plan'])
        if run['status'] == 'preparing':
            self._prepared(db, run, rows[0], plan)
        elif run['status'] == 'reviewing':
            cases = [row for row in rows if decode(row['payload'])['stage'] == 'case']
            if cases and all(row['status'] in TERMINAL for row in cases):
                self._aggregate(db, run, plan, cases)
        elif run['status'] == 'scoring':
            self._aggregated(db, run, rows)

    def _aggregated(self, db, run, rows):
        latest = next(row for row in rows if row['task_id'] == run['aggregate_id'])
        if latest['status'] in TERMINAL:
            result = decode(latest['result'])
            cases = [row for row in rows if decode(row['payload'])['stage'] == 'case']
            status = finished_status(latest['status'], cases, (result or {}).get('summary'))
            db.execute('UPDATE bench_scheduler_runs SET result=%s,status=%s,updated_at=UTC_TIMESTAMP(6) '
                       'WHERE scope=%s AND run_id=%s',
                       (encode(result), status, self.scope, run['run_id']))

    def _prepared(self, db, run, task, plan):
        if task['status'] not in TERMINAL:
            return
        if task['status'] != 'succeeded':
            self._status(db, run['run_id'], 'failed')
            return
        result = decode(task['result'])
        db.execute('UPDATE bench_scheduler_runs SET prepared=%s WHERE scope=%s AND run_id=%s',
                   (encode(result), self.scope, run['run_id']))
        if not plan['auto']:
            self._status(db, run['run_id'], 'ready')
            return
        self._cases(db, run['run_id'], plan, result)

    def _cases(self, db, run_id, plan, prepared):
        attempts = 1 if plan['spec']['evaluation_mode'] == 'stability' else 3
        for case in plan['cases']:
            payload = dict(stage='case', run_id=run_id, case=case, prepared=prepared, profile=plan['profile'])
            self._insert_task(db, run_id, case['id'], payload, plan['pool'], attempts)
        self._status(db, run_id, 'reviewing')

    def _aggregate(self, db, run, plan, cases, action='eval'):
        case_id = '__aggregate_' + uuid.uuid4().hex
        records = [dict(case_id=row['case_id'], status=row['status'], generation=row['generation'],
                        attempts=self._attempt_history(db, row['task_id']),
                        result=_evidence_pointer(row['result']), checkpoint=_evidence_pointer(row['checkpoint'])) for row in cases]
        prior = decode(run['result'])
        if action == 'eval' and (prior or {}).get('summary', {}).get('metrics'):
            action = 'report'  # Recovery must not overwrite already published scoring coverage.
        payload = dict(stage='aggregate', action=action, previous=prior, run_id=run['run_id'], prepared=decode(run['prepared']),
                       cases=records, profile=plan['profile'])
        self._insert_task(db, run['run_id'], case_id, payload, plan['pool'], 3)
        db.execute("UPDATE bench_scheduler_runs SET status='scoring',aggregate_id=%s WHERE scope=%s AND run_id=%s",
                   (task_identity(run['run_id'], case_id), self.scope, run['run_id']))

    def _status(self, db, run_id, status):
        db.execute('UPDATE bench_scheduler_runs SET status=%s,updated_at=UTC_TIMESTAMP(6) '
                   'WHERE scope=%s AND run_id=%s', (status, self.scope, run_id))

    def cancel_run(self, run_id):
        # Same lock order as claiming and publishing; cancellation cannot race a transition.
        with self._locked() as (db, _):
            db.execute("UPDATE bench_scheduler_runs SET status='cancelling' WHERE scope=%s AND run_id=%s "
                       "AND status IN ('preparing','reviewing','scoring')", (self.scope, run_id))
            db.execute("UPDATE bench_scheduler_tasks SET status=CASE WHEN status='queued' THEN 'cancelled' "
                       "ELSE 'cancelling' END WHERE scope=%s AND run_id=%s AND status IN ('queued','running')",
                       (self.scope, run_id))
            self._advance(db, run_id)

    def runs(self):
        with self.database.transaction() as db:
            db.execute('SELECT * FROM bench_scheduler_runs WHERE scope=%s ORDER BY created_at DESC', (self.scope,))
            runs = [{**row, **{key: decode(row[key]) for key in ('job', 'plan', 'prepared', 'result')}}
                    for row in db.fetchall()]
            # Old releases marked partial batches succeeded. Correct their read view too.
            for run in runs:
                if run['status'] == 'succeeded':
                    rows = self._rows(db, run['run_id'])
                    cases = [row for row in rows if decode(row['payload'])['stage'] == 'case']
                    run['status'] = finished_status('succeeded', cases, (run['result'] or {}).get('summary'))
            return runs

    def recover_agent(self, run_id, job, case_id, expected):
        from .recovery import requeue
        with self._locked() as (db, _):
            requeue(self, db, run_id, job, case_id, expected)

    def operate(self, run_id, job, action):
        with self._locked() as (db, _):
            db.execute('SELECT * FROM bench_scheduler_runs WHERE scope=%s AND run_id=%s', (self.scope, run_id))
            run = db.fetchone()
            if not run or run['status'] not in {'ready', 'succeeded', 'partial', 'failed'}:
                raise ValueError('批次尚未完成，不能重复执行操作')
            plan = decode(run['plan'])
            if plan.get('history_source'):
                raise ValueError('请在批次详情选择裁判并提交新的评分')
            if action == 'workflow' and run['status'] == 'ready':
                self._cases(db, run_id, plan, decode(run['prepared']))
            elif action in {'report', 'export', 'eval'} and run['prepared']:
                if action == 'eval' and decode(run['result'] or '{}').get('summary', {}).get('metrics'):
                    raise ValueError('已有评分不可覆盖；请新建批次')
                cases = [row for row in self._rows(db, run_id) if decode(row['payload'])['stage'] == 'case']
                if not cases:
                    raise ValueError('请先完成案例检视')
                self._aggregate(db, run, plan, cases, action)
            else:
                raise ValueError('集群支持整批检视、评分、报告和归档；单例重跑请新建批次')
            db.execute('UPDATE bench_scheduler_runs SET job=%s WHERE scope=%s AND run_id=%s',
                       (encode(job), self.scope, run_id))

    def worker_heartbeat(self, owner):
        with self.database.transaction() as db:
            db.execute('INSERT INTO bench_scheduler_workers VALUES(%s,%s,UTC_TIMESTAMP(6)) '
                       'ON DUPLICATE KEY UPDATE seen=UTC_TIMESTAMP(6)', (self.scope, owner))

    def meta(self, key):
        if key == 'recovery_required':
            return False
        if key != 'heartbeat':
            return None
        with self.database.transaction() as db:
            db.execute('SELECT UNIX_TIMESTAMP(MAX(seen)) AS heartbeat FROM bench_scheduler_workers '
                       'WHERE scope=%s AND seen>TIMESTAMPADD(SECOND,-15,UTC_TIMESTAMP(6))', (self.scope,))
            value = db.fetchone()['heartbeat']
            return float(value) if value else None

    def jobs(self):
        result = {}
        for row in self.runs():
            job = dict(row['job'])
            job['status'] = {'preparing':'queued', 'reviewing':'running', 'scoring':'running',
                             'ready':'succeeded'}.get(row['status'], row['status'])
            result[job['id']] = job
        return result

    def _attempt_history(self, db, task_id):
        db.execute('SELECT generation,status,checkpoint,result FROM bench_scheduler_attempts '
                   'WHERE scope=%s AND task_id=%s ORDER BY generation', (self.scope, task_id))
        return [{**row, 'checkpoint':_evidence_pointer(row['checkpoint']), 'result':_evidence_pointer(row['result'])}
                for row in db.fetchall()]


def _evidence_pointer(value):
    value = decode(value)
    if not value:
        return None
    return {key: value[key] for key in ('snapshot', 'log', 'archive', 'exit_code') if key in value}
