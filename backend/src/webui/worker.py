"""One independent worker per deployment; no automatic retry of paid calls."""
import argparse
import signal
import threading
import time
from .queue import DurableDashboard
from runner.diagnostics import event, phase
from runner.locking import acquire, release


def _execute_claimed(app, claimed):
    job, request, spec = claimed
    event('worker.claimed', job_id=job['id'], key=job['key'])
    app.jobs[job['id']] = job
    app.active = job['id']
    try:
        if app.fingerprint(job['profile']) != job['profile_fingerprint']:
            raise ValueError('排队期间模型或接口配置已变化，请重新提交')
        with phase('worker.execute', job_id=job['id'], key=job['key']):
            app.work(job['id'], request, spec)
    except Exception as error:
        event('worker.failed', job_id=job['id'], error_type=type(error).__name__)
        app.update(job['id'], status='failed', message='模型配置变化或不可读取；未启动测评')
    finally:
        event('worker.finished', job_id=job['id'], key=job['key'], status=app.jobs[job['id']]['status'])
        app.active = None


def _install_signals(app):
    def stop(signum, frame):
        event('worker.signal', signal=signum)
        app.stopping = True
    signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, 'SIGHUP'):
        signals.append(signal.SIGHUP)
    for sig in signals:
        signal.signal(sig, stop)


def _heartbeat(app, finished, failures):
    count = 0
    while not app.stopping and not finished.is_set():
        try:
            app.queue.meta('heartbeat', time.time())
        except Exception as error:
            event('worker.heartbeat.failed', error_type=type(error).__name__)
            failures.append(error)
            app.stopping = True
            return
        if count % 10 == 0:
            event('worker.heartbeat')
        count += 1
        finished.wait(3)


def _serve_worker(app, *, install_signals=True):
    if install_signals:
        _install_signals(app)
    finished = threading.Event()
    failures = []
    thread = threading.Thread(target=_heartbeat, args=(app, finished, failures), daemon=True)
    thread.start()
    try:
        while not app.stopping:
            # claim checks the recovery barrier in the same transaction as the claim.
            claimed = app.queue.claim()
            if claimed is None:
                time.sleep(.5)
                continue
            _execute_claimed(app, claimed)
    finally:
        app.stopping = True
        finished.set()
        thread.join(timeout=4)
        app.queue.meta('heartbeat', 0)
    if failures:
        raise RuntimeError('Worker 心跳失败，已停止领取任务') from failures[0]


def run_worker(app, *, install_signals=True, acknowledge=False):
    """Shared production and regression lifecycle, including the exclusive worker lock."""
    with (app.state / 'worker.lock').open('a+b') as lock:
        try:
            acquire(lock)
        except BlockingIOError:
            raise RuntimeError('已有 Worker 运行；恢复阻塞请在工作台确认') from None
        try:
            with phase('worker.recover'):
                app.queue.recover()
            if acknowledge:
                app.acknowledge_recovery(actor='administrator-cli')
                print('恢复阻塞已清除；中断任务未重试。')
                return
            if app.queue.meta('recovery_required'):
                print('Worker 曾中断，队列已暂停；网页可查看结果并确认恢复。', flush=True)
            with phase('worker.serve'):
                _serve_worker(app, install_signals=install_signals)
        finally:
            app.shutdown()
            release(lock)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ack-interrupted', action='store_true',
                        help='确认旧进程已停止后清除恢复阻塞，不重跑中断任务')
    args = parser.parse_args()
    event('worker.start')
    run_worker(DurableDashboard(), acknowledge=args.ack_interrupted)
if __name__=='__main__':main()
