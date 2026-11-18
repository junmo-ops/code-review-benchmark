"""Concurrent case workers using isolated subprocesses and immutable COS checkpoints."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from application.files import read, save
from runner.paths import PROJECT_ROOT
from runner.runtime import data_root, config_root
from .contracts import LeaseLost
from .execution import LeaseGuard
from .settings import services


def _restore(store, payload, checkpoint, data):
    run = data / 'runs' / payload['run_id']
    if payload['stage'] == 'prepare':
        store.restore(payload['input'], data / 'input')
    else:
        store.restore(payload['prepared']['snapshot'], run)
    if payload['stage'] == 'aggregate':
        if payload.get('previous') and payload['previous'].get('snapshot'):
            store.restore(payload['previous']['snapshot'], run)
        for case in payload['cases']:
            record = case['result'] or case['checkpoint']
            if record and record.get('snapshot'):
                store.restore(record['snapshot'], run)
        save(run / 'cluster-attempts.json', payload['cases'])
    if checkpoint and checkpoint.get('snapshot'):
        store.restore(checkpoint['snapshot'], run)
    run.mkdir(parents=True, exist_ok=True)
    return run


def _environment(payload, data):
    from deployment.model_storage import ModelProfileStorage
    from local import ALLOWED
    profile = payload['profile']
    storage = ModelProfileStorage(PROJECT_ROOT)
    storage.restore(storage._key(profile))
    config = read(config_root(PROJECT_ROOT) / profile)
    if set(config) - ALLOWED:
        raise ValueError('固定模型配置字段无效')
    env = dict(os.environ, **config)
    env.update(BENCH_DATA_ROOT=str(data), BENCH_CONFIG_ROOT=str(config_root(PROJECT_ROOT)),
               BENCH_CLUSTER_TASK='1', BENCH_CLUSTER_BARRIER=str(data / 'checkpoint.request'),
               PYTHONUNBUFFERED='1', PYTHONUTF8='1')
    return env


def _tail(path):
    if not path.exists():
        return ''
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - 200000))
        return stream.read(200000).decode('utf-8', 'replace')


def _snapshot(store, run, log, app):
    # The task log exposed to users is always redacted; credentials live outside the run.
    target = run / 'cluster-execution.log'
    text = _tail(log)
    target.write_text(app.redact(text[-200000:]), encoding='utf-8')
    return {'snapshot': store.capture(run), 'log': store.put(target)}


def _monitor(process, store, run, data, log, guard, stop, app, *, resource=False):
    checkpoint_at, acknowledged = 0, ''
    request = data / 'checkpoint.request'
    deadline = time.monotonic() + 172800
    while process.poll() is None:
        guard.check()
        if stop.is_set() or time.monotonic() > deadline:
            raise LeaseLost('Worker 停止或任务超过两天上限；不发布本次结果')
        token = request.read_text(encoding='ascii') if request.exists() else ''
        barrier = token and token != acknowledged
        interval = 5 if resource else 30
        if barrier or time.monotonic() - checkpoint_at > interval:
            record = _resource_checkpoint(store, data, log, app) if resource else _snapshot(store, run, log, app)
            guard.checkpoint(record)
            checkpoint_at = time.monotonic()
            if barrier:
                request.with_suffix('.ack').write_text(token, encoding='ascii')
                acknowledged = token
        time.sleep(.3)


def _resource_checkpoint(store, data, log, app):
    target = data / 'resource-execution.log'
    target.write_text(app.redact(_tail(log)), encoding='utf-8')
    progress = data / 'resource-progress.json'
    return dict(log=store.put(target), progress=json.loads(app.redact(progress.read_text(encoding='utf-8'))) if progress.exists() else {})


def _task_environment(payload, data):
    if payload['stage'] != 'resource':
        return _environment(payload, data)
    return dict(os.environ, BENCH_DATA_ROOT=str(data), PYTHONUNBUFFERED='1', PYTHONUTF8='1')


def _process(payload, data, run, log, store, guard, stop, app):
    from local import terminate
    resource = payload['stage'] == 'resource'
    request = data / 'task.json'
    save(request, payload)
    env = _task_environment(payload, data)
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    module = 'scheduling.resource_task' if resource else 'scheduling.task'
    with log.open('w', encoding='utf-8') as output:
        guard.check()
        if stop.is_set():
            raise LeaseLost('Worker 正在停止')
        process = subprocess.Popen([sys.executable, '-m', module, str(request)], cwd=PROJECT_ROOT,
                                   env=env, stdout=output, stderr=subprocess.STDOUT, **options)
        try:
            _monitor(process, store, run, data, log, guard, stop, app, resource=resource)
        finally:
            if process.poll() is None:
                terminate(process)
    return process.returncode


def _result(payload, store, run, data, log, app):
    if payload['stage'] == 'resource':
        path = data / 'resource-result.json'
        record = read(path) if path.exists() else {'error': '资源子进程未返回结果，请查看 Worker 日志'}
        return dict(record, **_resource_checkpoint(store, data, log, app))
    record = _snapshot(store, run, log, app)
    summary = data / 'task-summary.json'
    if summary.exists():
        record['summary'] = read(summary)
    archive = run.parent / (run.name + '-results.tar.gz')
    if archive.exists():
        record['archive'] = store.put(archive)
    return record


def _execute(queue, store, lease, settings, stop):
    from webui.service import Dashboard
    payload = dict(lease.payload, takeover=lease.generation > 1)
    if payload['runtime_key'] not in _runtimes():
        raise LeaseLost('执行工具版本不匹配；等待兼容 Worker')
    base = data_root(PROJECT_ROOT) / 'cluster-work'
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=lease.task_id[:12] + '-', dir=base) as temporary:
        data = Path(temporary)
        with LeaseGuard(queue, lease, settings['lease_seconds']) as guard:
            run = data if payload['stage'] == 'resource' else _restore(store, payload, lease.checkpoint, data)
            app = Dashboard(load_jobs=False)
            log = data / 'process.log'
            code = _process(payload, data, run, log, store, guard, stop, app)
            record = _result(payload, store, run, data, log, app)
            record.update(exit_code=code, runtime_key=payload['runtime_key'])
            guard.check()
            queue.finish(lease, record, succeeded=code == 0)


def _attempt(queue, store, lease, settings, stop):
    try:
        from .cos_maintenance import writer
        with writer(queue, 'worker'):
            _execute(queue, store, lease, settings, stop)
    except Exception as error:
        # No uncertain attempt can publish. Expiration records loss and bounds takeovers.
        print(f'集群尝试未发布：{lease.task_id}/{lease.generation} {type(error).__name__}', flush=True)


def main():
    queue, store, settings = services()
    queue.runs()
    owner = 'worker_' + uuid.uuid4().hex
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    runtimes = _runtimes()
    print('集群 Worker：' + owner, flush=True)
    from .health import WorkerHealth
    health = WorkerHealth(owner)
    health.tick()
    try:
        with ThreadPoolExecutor(max_workers=settings['worker_slots']) as executor:
            _dispatch(queue, store, settings, owner, stop, runtimes, executor, health)
    finally:
        health.close()


def _dispatch(queue, store, settings, owner, stop, runtimes, executor, health):
    from .web_state import WebState
    state = WebState(queue)
    futures = set()
    try:
        while not stop.is_set():
            futures = {future for future in futures if not future.done()}
            health.tick()
            try:
                queue.worker_heartbeat(owner)
                queue.reconcile()
                if len(futures) < settings['worker_slots']:
                    lease = queue.claim(owner, settings['lease_seconds'], runtimes)
                    if lease:
                        futures.add(executor.submit(_attempt, queue, store, lease, settings, stop))
                health.tick(ready=bool(state.get('resources') and state.get('models')))
            except Exception as error:
                health.tick(ready=False)
                print('集群调度暂不可用：' + type(error).__name__, flush=True)
            stop.wait(.5)
    finally:
        # Report draining before waiting for child processes and executor shutdown.
        stop.set()
        health.close()


def _runtimes():
    from .settings import runtime_key
    from .resources import RUNTIME
    keys = [RUNTIME]
    for reviewer in ('lcr', 'server_agent', 'dsh_skill'):
        try:
            keys.append(runtime_key(reviewer))
        except (OSError, ValueError, RuntimeError):
            print('本实例未提供被测工具：' + reviewer, flush=True)
    return keys


if __name__ == '__main__':
    main()
