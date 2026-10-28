"""Isolated local HTTP server and durable queue worker for browser acceptance."""
import argparse
from contextlib import ExitStack
import json
import hashlib
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
        def dataset_path(self, dataset_id):
            # Exercise the built-in selection ID without changing production case bytes.
            if os.environ.get('E2E_BUILTIN_CASE') == '1' and dataset_id == 'pilot1.jsonl':
                return self.data / 'fixtures/builtin-pilot1.jsonl'
            return super().dataset_path(dataset_id)

        def command(self, key, stage, profile, task=None, retry=False, init=None):
            cfg = init if init is not None else self.config(key)
            if cfg.get('reviewer') == 'dsh_skill':
                return super().command(key, stage, profile, task, retry, init)
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
    if os.environ.get('E2E_DSH_RUNTIME_MODE'):
        os.environ['BENCH_DSH_RUNTIME_MODE'] = os.environ['E2E_DSH_RUNTIME_MODE']
    stack = ExitStack()
    if os.environ.get('E2E_DSH') == '1':
        from tools.dsh_local_model import serve
        local_model = stack.enter_context(serve())
        local_model.respond = dsh_response(data)
        seed_dsh_models(data, local_model.url)
    if not args.reuse:
        seed_models(data)
        from fixtures import create
        create(data)
    from skill_storage_fixture import enable as enable_skill_storage
    enable_skill_storage(stack, data, ROOT)
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
        stack.close()


def seed_dsh_models(data, url):
    for name, model in [('review', 'dsh-review'), ('judge', 'dsh-judge')]:
        path = data / 'config' / f'model.dsh.{name}.local.json'
        path.write_text(json.dumps(dict(LCR_LLM_BASE_URL=url, LCR_LLM_TOKEN='dsh-local-only',
                                      LCR_LLM_MODEL=model, LCR_LLM_STREAM='false')))
        path.with_suffix('.label').write_text('DSH 本地' + name)


def dsh_response(data):
    from runner.storage import save
    from tools.dsh_local_model import completion
    path = data / 'dsh-model-requests.json'
    records = json.loads(path.read_text()) if path.exists() else []
    lock = threading.Lock()

    def response(body, index):
        with lock:
            records.append(dict(model=body['model'], at=time.time(),
                                sha256=hashlib.sha256(json.dumps(body).encode()).hexdigest()))
            save(path, records)
            review_calls = sum(row['model'] == 'dsh-review' for row in records)
        control = data / 'dsh-model-control.json'
        deadline = time.monotonic() + 90
        while control.exists() and time.monotonic() < deadline:
            settings = json.loads(control.read_text())
            if body['model'] != 'dsh-review' or review_calls <= settings['hold_after_review_calls']:
                break
            time.sleep(.05)
        return completion(body, index)

    return response


if __name__ == '__main__':
    main()
