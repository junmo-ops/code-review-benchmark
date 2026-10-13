"""Single-host durable queue. Credentials are never serialized into jobs."""
import contextlib
import hashlib
import json
import os
import sqlite3
import time
from .service import Dashboard, ROOT, ACTIVE, now

class Queue:
    def __init__(self,path):
        self.path=path;path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, run_key TEXT NOT NULL, status TEXT NOT NULL,
                job TEXT NOT NULL, request TEXT NOT NULL, spec TEXT, cancel INTEGER NOT NULL DEFAULT 0);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_per_run ON jobs(run_key) WHERE status IN ('queued','running','cancelling');
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);''')
    @contextlib.contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15)
        db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def insert(self,job,request,spec):
        try:
            with self.connect() as db:
                db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,0)',(job['id'],job['key'],job['status'],json.dumps(job),json.dumps(request),json.dumps(spec)))
        except sqlite3.IntegrityError as exc:raise ValueError('该批次已有排队或运行任务') from exc
    def jobs(self):
        with self.connect() as db:return {r['id']:json.loads(r['job']) for r in db.execute('SELECT id,job FROM jobs')}
    def update(self,id,changes):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT job FROM jobs WHERE id=?',(id,)).fetchone()
            job=json.loads(row['job']);job.update(changes)
            db.execute('UPDATE jobs SET job=?,status=? WHERE id=?',(json.dumps(job),job['status'],id))
            return job
    def claim(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if not row:return None
            job=json.loads(row['job']);job.update(status='running',started_at=now(),message='Worker 已领取')
            db.execute('UPDATE jobs SET status=?,job=? WHERE id=?',('running',json.dumps(job),job['id']))
            return job,json.loads(row['request']),json.loads(row['spec'])
    def stop(self,id,actor=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM jobs WHERE id=?',(id,)).fetchone()
            if not row or row['status'] not in ACTIVE:raise ValueError('任务不存在或已结束')
            job=json.loads(row['job']);queued=row['status']=='queued'
            job.update(status='cancelled' if queued else 'cancelling',message='已取消排队' if queued else '正在停止任务')
            if actor:job.update(stopped_by=actor,stop_requested_at=now())
            if queued:job['ended_at']=now()
            db.execute('UPDATE jobs SET cancel=1,status=?,job=? WHERE id=?',(job['status'],json.dumps(job),id))
    def cancelled(self,id):
        with self.connect() as db:
            row=db.execute('SELECT cancel FROM jobs WHERE id=?',(id,)).fetchone()
            return bool(row and row['cancel'])
    def meta(self,key,value=None):
        with self.connect() as db:
            if value is not None:db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,json.dumps(value)))
            row=db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
            return json.loads(row['value']) if row else None
    def recover(self):
        # Called only while holding the exclusive worker lock. Do not replay interrupted jobs.
        for job in self.jobs().values():
            if job['status'] in {'running','cancelling'}:
                self.update(job['id'],dict(status='interrupted',ended_at=now(),message='Worker 曾中断；确认旧进程与容器已停止后，人工恢复'))
                self.meta('recovery_required',True)

    def acknowledge_recovery(self, actor):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('running','cancelling') LIMIT 1").fetchone():
                raise ValueError('仍有运行中的任务，不能解除恢复阻塞')
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',
                       ('recovery_required', json.dumps(False)))
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',
                       ('recovery_acknowledged', json.dumps({'actor': actor, 'at': now()})))

class Cancellation:
    def __init__(self,app):self.app=app
    def is_set(self):
        return self.app.stopping or bool(self.app.active and self.app.queue.cancelled(self.app.active))
    def wait(self,seconds):time.sleep(seconds);return self.is_set()
    def clear(self):pass
    def set(self):self.app.stopping=True

class DurableDashboard(Dashboard):
    queue_mode=True
    def __init__(self,root=ROOT):
        super().__init__(root,load_jobs=False)
        (self.state/'jobs').mkdir(exist_ok=True)
        self.queue=Queue(self.state/'queue.sqlite3');self.stopping=False
        self.cancelled=Cancellation(self)
        self.sync()
    def sync(self):
        self.jobs=self.queue.jobs()
        self.active=next((j['id'] for j in self.jobs.values() if j['status'] in {'running','cancelling'}),None)
    def fingerprint(self,profile):
        env=self.environment(profile)
        # Keys may rotate; model and endpoint choices must not change while queued.
        names=('LCR_LLM_MODEL','LCR_LLM_BASE_URL','JUDGE_MODEL','JUDGE_BASE_URL','JUDGE_USE_MOCK')
        return hashlib.sha256(json.dumps({k:env.get(k) for k in names},sort_keys=True).encode()).hexdigest()
    def enqueue(self,job,request,spec):
        from .case_import import ensure_idle
        ensure_idle(self)
        actor=request.get('_actor','local')
        request={k:v for k,v in request.items() if k in {'action','key','suite','dataset','name','profile','reviewer','evaluator','timeout_minutes','case_jobs','max_tokens','task','retry','start_after_create'}}
        job['profile_fingerprint']=self.fingerprint(job['profile'])
        job['submitted_by']=actor
        self.queue.insert(job,request,spec)
        self.sync()
        return dict(job_id=job['id'],key=job['key'])
    def submit(self, request):
        self.sync()
        if request.get('action') != 'resume':
            return super().submit(request)
        from .resume import prepare_resume
        prepared = prepare_resume(self, request)
        result = super().submit(prepared)
        # Enqueue first: invalid requests must not silently unblock the queue.
        if self.queue.meta('recovery_required'):
            self.acknowledge_recovery(request.get('_actor', 'local'))
        return result
    def update(self,id,**changes):
        if changes.get('status') == 'cancelled' and self.stopping and not self.queue.cancelled(id):
            changes.update(status='interrupted', message='服务停止，批次已中断；确认旧进程停止后可继续未完成案例并评分')
            self.queue.meta('recovery_required', True)
        self.jobs[id]=self.queue.update(id,changes)
    def describe(self,key):
        out=super().describe(key)
        jobs=[j for j in self.jobs.values() if j['key']==key]
        if jobs:out['profile']=max(jobs,key=lambda j:j['created_at'])['profile']
        return out
    def snapshot(self):
        self.sync();out=super().snapshot();out['queue_mode']=True
        heartbeat=self.queue.meta('heartbeat') or 0
        out['worker_ready']=time.time()-heartbeat<15 and not self.queue.meta('recovery_required')
        out['recovery_required']=bool(self.queue.meta('recovery_required'))
        return out
    def logs(self,id):self.sync();return super().logs(id)
    def acknowledge_recovery(self, actor):
        self.queue.acknowledge_recovery(actor)
        if os.environ.get('BENCH_RUN_STORAGE_MODE', 'local').strip().lower() == 'cos':
            from deployment.run_storage import request_run_sync
            request_run_sync(self.data)
    def stop(self,id,actor=None):self.queue.stop(id,actor)
    def shutdown(self):pass  # Web shutdown must not stop a worker job.
