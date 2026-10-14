"""Explicit continuation of a stopped LCR batch, never automatic paid replay."""
import time
from runner.cloud import exclusive


def prepare_resume(app, request):
    if request.get('confirmed') is not True:
        raise ValueError('请确认旧扫描进程和容器已经停止，再继续未完成案例')
    key = request.get('key')
    cfg = app.config(key)
    if key.startswith(('mcr/', 'ccrab/')) or cfg.get('reviewer', 'lcr') != 'lcr':
        raise ValueError('当前仅支持单轮 LCR 批次继续执行')
    jobs = [job for job in app.jobs.values() if job['key'] == key]
    latest = max(jobs, key=lambda job: job['created_at']) if jobs else None
    if not latest or latest['status'] not in {'interrupted', 'cancelled'}:
        raise ValueError('该批次没有可继续执行的已停止或中断任务')
    queue = getattr(app, 'queue', None)
    if queue and time.time() - (queue.meta('heartbeat') or 0) >= 15:
        raise ValueError('Worker 尚未就绪，请稍后继续')
    if latest['status'] == 'interrupted' and any(job['status'] in {'running', 'cancelling'} for job in app.jobs.values()):
        raise ValueError('仍有任务执行中，不能恢复中断批次')
    with exclusive(app.run_dir(key)):
        pass  # Check the runner lock before queueing; execution reacquires it.
    return dict(request, action='workflow', retry=True, task=None)
