"""Isolated local HTTP server and durable queue worker for browser acceptance."""
import argparse
import json
import os
from pathlib import Path
import sys
import signal
import threading
import time
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'backend/src'), str(ROOT)]


def environment(data, reuse=False):
    for name in list(os.environ):
        if name.startswith(('LCR_', 'JUDGE_', 'BENCH_')):
            os.environ.pop(name)
    os.environ.update(BENCH_PROJECT_ROOT=str(ROOT), BENCH_DATA_ROOT=str(data),
                      BENCH_CONFIG_ROOT=str(data / 'config'), BENCH_CASE_UPLOAD='1',
                      BENCH_CASE_STORAGE_MODE='local', BENCH_RUN_STORAGE_MODE='local',
                      BENCH_MODEL_STORAGE_MODE='local', PYTHONUTF8='1',
                      BENCH_FRONTEND_DIST=str(data.parent / 'frontend-dist'),
                      PYTHONPATH=os.pathsep.join([str(ROOT / 'backend/src'), str(ROOT)]),
                      HOME=str(data / 'home'), USERPROFILE=str(data / 'home'))
    (data / 'config').mkdir(parents=True, exist_ok=reuse)
    (data / 'home').mkdir(exist_ok=reuse)


def seed_models(data):
    config = dict(LCR_LLM_BASE_URL='http://127.0.0.1:9/v1', LCR_LLM_TOKEN='e2e-dummy',
                  LCR_LLM_MODEL='e2e-success', JUDGE_BASE_URL='http://127.0.0.1:9/v1',
                  JUDGE_API_KEY='e2e-dummy', JUDGE_MODEL='e2e-judge', LCR_LLM_STREAM='false')
    for name in ('success', 'partial', 'retry', 'slow'):
        path = data / 'config' / f'model.deepseek.{name}.local.json'
        path.write_text(json.dumps(dict(config, LCR_LLM_MODEL='e2e-' + name)))
        path.with_suffix('.label').write_text('回归-' + name)


def make_dashboard():
    from webui.queue import DurableDashboard
    from simulation import configure
    configure()

    class RegressionDashboard(DurableDashboard):
        def command(self, key, stage, profile, task=None, retry=False, init=None):
            spec = dict(init or {}, task=task, retry=retry)
            if stage == 'review':
                spec['external_timeout_seconds'] = super().command(key, stage, profile, task, retry, init)[2]
            if init:
                spec['dataset'] = str(self.dataset_path(init['dataset']))
            return [sys.executable, str(ROOT / 'scripts/e2e/stage.py'), stage, key,
                    json.dumps(spec)], self.environment(profile), int(os.environ.get('E2E_STAGE_TIMEOUT_SECONDS', '120'))
    return RegressionDashboard(ROOT)


def worker(app, stop):
    holder = [type(app)(ROOT)]
    threading.Thread(target=interrupt_control, args=(holder, stop), daemon=True).start()
    threading.Thread(target=worker_heartbeat, args=(holder, stop), daemon=True).start()
    worker_app = holder[0]
    worker_app.queue.recover()
    while not stop.is_set():
        worker_app.queue.meta('heartbeat', time.time())
        if not worker_app.queue.meta('recovery_required'):
            claimed = worker_app.queue.claim()
            if claimed:
                job, request, spec = claimed
                worker_app.jobs[job['id']] = job
                worker_app.active = job['id']
                if worker_app.fingerprint(job['profile']) != job['profile_fingerprint']:
                    worker_app.update(job['id'], status='failed', message='排队期间模型或接口配置已变化')
                else:
                    worker_app.work(job['id'], request, spec)
        if worker_app.stopping:
            worker_app = type(app)(ROOT)
            holder[0] = worker_app
            worker_app.queue.recover()
        stop.wait(.1)
    worker_app.shutdown()


def worker_heartbeat(holder, stop):
    while not stop.is_set():
        if not holder[0].stopping:
            holder[0].queue.meta('heartbeat', time.time())
        stop.wait(1)
    holder[0].queue.meta('heartbeat', 0)


def interrupt_control(holder, stop):
    marker = Path(os.environ['BENCH_DATA_ROOT']) / 'interrupt-worker'
    while not stop.wait(.1):
        if marker.exists():
            marker.unlink()
            holder[0].stopping = True
    holder[0].stopping = True


def cgroup_limit():
    for name in ('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory/memory.limit_in_bytes'):
        try:
            value = Path(name).read_text().strip()
            return int(value) if value != 'max' else None
        except (OSError, ValueError):
            continue
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True)
    parser.add_argument('--reuse', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    data = Path(args.data).resolve()
    if (data / 'config').exists() and not args.reuse:
        raise ValueError('Use a new isolated regression directory')
    environment(data, args.reuse)
    if not args.reuse:
        seed_models(data)
        from fixtures import create
        create(data)
    from webui.server import handler_for
    app = make_dashboard()
    stop = threading.Event()
    thread = threading.Thread(target=worker, args=(app, stop), daemon=True)
    thread.start()
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((args.host, args.port), handler_for(app, 'e2e-local-session'))
    def terminate(signum, frame):
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, terminate)
    (data / 'server.json').write_text(json.dumps({'url': f'http://127.0.0.1:{server.server_port}', 'pid': os.getpid(), 'containerized': args.host != '127.0.0.1', 'memory_limit_bytes': cgroup_limit()}))
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()
        thread.join(timeout=15)


if __name__ == '__main__':
    main()
