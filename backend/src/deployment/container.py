"""Single-container Web/Worker trial; no automatic model requests or paid retries."""
import argparse
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time
import uuid
from runner.diagnostics import event, phase

from runner.paths import PROJECT_ROOT as ROOT


def check_build():
    import json
    from runner.cloud import checked_instances
    from reviewers.lcr.adapter import tool_identity
    manifest = json.loads((ROOT / 'deployment/aacr-cases-manifest.json').read_text(encoding='utf-8'))
    cases = checked_instances(ROOT / 'data/pilot5.jsonl', 'aacr')
    if not manifest['files'] or not cases:
        raise RuntimeError('AACR 案例清单或资源校验清单为空')
    frontend=Path(os.environ.get('BENCH_FRONTEND_DIST',ROOT/'frontend-dist'))
    if not (frontend/'index.html').is_file() or not any((frontend/'assets').glob('*')):
        raise RuntimeError('React 前端构建产物缺失')
    tool_identity()
    _check_dsh(runtime=False)
    print('Python、React 前端、AACR 源码和 LCR 资源检查完成；未调用模型。', flush=True)


def prepare():
    import json
    _check_dsh(runtime=True)
    print('运行 Python：' + sys.version.split()[0], flush=True)
    manifest = json.loads((ROOT / 'lcr/package.json').read_text(encoding='utf-8'))
    print('运行 LCR npm 包：' + manifest['version'], flush=True)
    from runner.runtime import data_root, config_root
    for path in (data_root(ROOT), config_root(ROOT), Path(os.environ['HOME'])):
        path.mkdir(parents=True, exist_ok=True)
    from deployment.run_storage import restore_run_data
    restore_run_data(ROOT)
    from deployment.model_storage import restore_profiles
    restore_profiles(ROOT)
    from webui.case_storage import restore_case_archives
    restore_case_archives(ROOT)
    print('工作台运行目录已准备；案例包可在网页上传，未调用模型。', flush=True)


def _check_dsh(*, runtime):
    from deployment import dsh_preflight
    if not dsh_preflight.enabled():
        print('DSH disabled (BENCH_DSH_ENABLED=0); no DSH readiness claim.', flush=True)
        return
    if runtime:
        dsh_preflight.check_runtime()
    else:
        dsh_preflight.check_build()


def _launch(command, children):
    child = subprocess.Popen(command, cwd=ROOT, start_new_session=True)
    children.append(child)
    return child


def _wait_preparation(stop, children):
    process = _launch([sys.executable, '-m', 'deployment.container', '--prepare'], children)
    event('container.child_started', child_pid=process.pid, stage='prepare')
    while process.poll() is None and not stop.wait(.2):
        pass
    return process.returncode, stop.is_set()


def _start_synchronizer(role):
    enabled = role in {'all', 'worker'} and os.environ.get('BENCH_RUN_STORAGE_MODE', 'local').strip().lower() == 'cos'
    if not enabled:
        return None
    from deployment.run_storage import RunDataStorage, RunDataSynchronizer
    interval = int(os.environ.get('BENCH_RUN_STORAGE_INTERVAL_SECONDS', '30'))
    synchronizer = RunDataSynchronizer(RunDataStorage(ROOT), interval)
    synchronizer.start()
    return synchronizer


def _start_services(role, port, children):
    services = []
    if role in {'all', 'worker'}:
        child = _launch([sys.executable, '-m', 'webui.worker'], children)
        services.append(child)
        event('container.child_started', child_pid=child.pid, stage='worker')
    if role in {'all', 'web'}:
        command = [sys.executable, '-m', 'gunicorn', '--bind', f'0.0.0.0:{port}',
                   '--workers', '1', '--threads', '4', '--timeout', '120',
                   '--access-logfile', '-', 'webui.wsgi:create_app()']
        child = _launch(command, children)
        services.append(child)
        event('container.child_started', child_pid=child.pid, stage='web')
    return services


def _monitor_services(stop, services):
    while not stop.wait(.2):
        for process in services:
            if process.poll() is not None:
                event('container.child_exit', child_pid=process.pid, exit_code=process.returncode)
                print(f'服务进程退出：pid={process.pid}，exit_code={process.returncode}；停止本容器其余进程。', flush=True)
                return process.returncode or 1
    return 0


def _track_descendants(children):
    from local import descendants
    tracked = set()
    for process in children:
        if process.poll() is not None:
            continue
        try:
            tracked.update(descendants(process.pid))
        except (OSError, subprocess.CalledProcessError):
            print('无法读取进程树；仍向服务进程组发送停止信号。', flush=True)
    return tracked


def _signal_groups(children, sig):
    for process in children:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass


def _wait_children(children):
    deadline = time.monotonic() + 25
    for process in children:
        try:
            process.wait(timeout=max(.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass


def _kill_descendants(tracked):
    for pid in sorted(tracked, reverse=True):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _shutdown(children, synchronizer, previous):
    tracked = _track_descendants(children)
    # Gracefully stop workers before the final data sync, then kill leftover children.
    _signal_groups(children, signal.SIGTERM)
    _wait_children(children)
    _signal_groups(children, signal.SIGKILL)
    _kill_descendants(tracked)
    for process in children:
        process.wait()
    if synchronizer:
        synchronizer.stop()
    for sig, handler in previous.items():
        signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-build', action='store_true')
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    if args.check_build:
        check_build()
    elif args.prepare:
        prepare()
    else:
        raise SystemExit(_run_container())


def _run_container():
    role = os.environ.get('BENCH_CONTAINER_ROLE', 'all')
    if role not in {'all', 'web', 'worker'}:
        raise ValueError('BENCH_CONTAINER_ROLE 须为 all、web 或 worker')
    if os.environ.get('BENCH_AUTH_MODE', 'proxy') == 'basic':
        # This token belongs to one Web lifetime; a restart requires a page refresh.
        os.environ.setdefault('BENCH_CSRF_TOKEN', secrets.token_urlsafe(48))
    port = int(os.environ.get('PORT', '8080'))
    if not 1 <= port <= 65535:
        raise ValueError('PORT 须为 1–65535')
    os.environ['BENCH_DIAGNOSTIC_BOOT_ID'] = uuid.uuid4().hex
    event('container.start')
    from .resource_monitor import ResourceMonitor
    monitor = ResourceMonitor()
    monitor.start()
    stop = threading.Event()
    previous = {}
    def stop_container(signum, frame):
        event('container.signal', signal=signum)
        stop.set()
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous[sig] = signal.signal(sig, stop_container)
    children = []
    synchronizer = None
    try:
        with phase('container.prepare'):
            preparation_code, stopped = _wait_preparation(stop, children)
        if stopped:
            return 0
        if preparation_code:
            return preparation_code
        synchronizer = _start_synchronizer(role)
        # Validate Web configuration before starting a Worker or listening.
        if role in {'all', 'web'}:
            from webui.wsgi import create_app
            create_app()
        if os.environ.get('BENCH_STORAGE_MODE') == 'ephemeral':
            print('试运行：容器重建会丢失进度、配置和结果，请及时导出。', flush=True)
        services = _start_services(role, port, children)
        event('container.services_started')
        return _monitor_services(stop, services)
    finally:
        try:
            with phase('container.shutdown'):
                _shutdown(children, synchronizer, previous)
        finally:
            monitor.stop()
            event('container.end')


if __name__ == '__main__':
    main()
