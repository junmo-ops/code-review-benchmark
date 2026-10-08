"""One independent worker per deployment; no automatic retry of paid calls."""
import argparse
import fcntl
import signal
import threading
import time
from .queue import DurableDashboard

def main():
    p=argparse.ArgumentParser();p.add_argument('--ack-interrupted',action='store_true',help='仅在确认旧进程和容器已停止后清除恢复阻塞，不重跑中断任务')
    args=p.parse_args();app=DurableDashboard()
    with (app.state/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('已有 Worker 运行')
        app.queue.recover()
        if args.ack_interrupted:
            app.queue.meta('recovery_required',False)
            print('恢复阻塞已清除；中断任务未重试。');return
        if app.queue.meta('recovery_required'):raise SystemExit('请先确认旧进程/容器停止，再运行 --ack-interrupted；待执行队列已保留。')
        def stop(signum,frame):app.stopping=True
        for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):signal.signal(sig,stop)
        def heartbeat():
            while not app.stopping:
                app.queue.meta('heartbeat',time.time());time.sleep(3)
        thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
        try:
            while not app.stopping:
                claimed=app.queue.claim()
                if not claimed:time.sleep(.5);continue
                job,request,spec=claimed
                app.jobs[job['id']]=job;app.active=job['id']
                try:
                    if app.fingerprint(job['profile'])!=job['profile_fingerprint']:
                        raise ValueError('排队期间模型或接口配置已变化，请重新提交')
                    app.work(job['id'],request,spec)
                except Exception:
                    app.update(job['id'],status='failed',message='模型配置变化或不可读取；未启动测评')
                finally:app.active=None
        finally:app.queue.meta('heartbeat',0)
if __name__=='__main__':main()
