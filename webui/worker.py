"""One independent worker per deployment; no automatic retry of paid calls."""
import argparse
import fcntl
import signal
import threading
import time
from .queue import DurableDashboard


def _execute_claimed(app, claimed):
    job, request, spec = claimed
    app.jobs[job['id']] = job
    app.active = job['id']
    try:
        if app.fingerprint(job['profile']) != job['profile_fingerprint']:
            raise ValueError('排队期间模型或接口配置已变化，请重新提交')
        app.work(job['id'], request, spec)
    except Exception:
        app.update(job['id'], status='failed', message='模型配置变化或不可读取；未启动测评')
    finally:
        app.active = None


def _serve_worker(app):
    def stop(signum, frame):
        app.stopping = True
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)

    def heartbeat():
        while not app.stopping:
            app.queue.meta('heartbeat', time.time())
            time.sleep(3)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        while not app.stopping:
            if app.queue.meta('recovery_required'):
                time.sleep(.5)
                continue
            claimed = app.queue.claim()
            if claimed is None:
                time.sleep(.5)
                continue
            _execute_claimed(app, claimed)
    finally:
        app.stopping = True
        thread.join(timeout=4)
        app.queue.meta('heartbeat', 0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ack-interrupted', action='store_true',
                        help='确认旧进程已停止后清除恢复阻塞，不重跑中断任务')
    args = parser.parse_args()
    app = DurableDashboard()
    with (app.state/'worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('已有 Worker 运行；恢复阻塞请在工作台确认') from None
        app.queue.recover()
        if args.ack_interrupted:
            app.acknowledge_recovery(actor='administrator-cli')
            print('恢复阻塞已清除；中断任务未重试。')
            return
        if app.queue.meta('recovery_required'):
            print('Worker 曾中断，队列已暂停；网页可查看结果并确认恢复。', flush=True)
        _serve_worker(app)
if __name__=='__main__':main()
