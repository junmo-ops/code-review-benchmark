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

ROOT = Path(__file__).resolve().parents[1]


def check_build():
    import json
    from runner.cloud import checked_instances
    from reviewers.lcr.adapter import tool_identity
    manifest = json.loads((ROOT / 'deployment/aacr-cases-manifest.json').read_text(encoding='utf-8'))
    cases = checked_instances(ROOT / 'data/pilot5.jsonl', 'aacr')
    if not manifest['files'] or not cases:
        raise RuntimeError('AACR 案例清单或资源校验清单为空')
    tool_identity()
    print('Python、AACR 源码和 LCR 资源检查完成；未调用模型。', flush=True)


def prepare():
    import json
    print('运行 Python：' + sys.version.split()[0], flush=True)
    manifest = json.loads((ROOT / 'lcr/package.json').read_text(encoding='utf-8'))
    print('运行 LCR npm 包：' + manifest['version'], flush=True)
    from runner.runtime import data_root, config_root
    for path in (data_root(ROOT), config_root(ROOT), Path(os.environ['HOME'])):
        path.mkdir(parents=True, exist_ok=True)
    print('工作台运行目录已准备；案例包请部署后在网页上传，未调用模型。', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-build', action='store_true')
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    if args.check_build:
        check_build()
        return 0
    if args.prepare:
        prepare()
        return 0
    role = os.environ.get('BENCH_CONTAINER_ROLE', 'all')
    if role not in {'all', 'web', 'worker'}:
        raise ValueError('BENCH_CONTAINER_ROLE 须为 all、web 或 worker')
    if os.environ.get('BENCH_AUTH_MODE', 'proxy') == 'basic':
        # This token belongs to one Web lifetime; a restart requires a page refresh.
        os.environ.setdefault('BENCH_CSRF_TOKEN', secrets.token_urlsafe(48))
    port = int(os.environ.get('PORT', '8080'))
    if not 1 <= port <= 65535:
        raise ValueError('PORT 须为 1–65535')
    stop = threading.Event()
    previous = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous[sig] = signal.signal(sig, lambda *_: stop.set())
    children = []

    def launch(command):
        child = subprocess.Popen(command, cwd=ROOT, start_new_session=True)
        children.append(child)
        return child

    try:
        preparation = launch([sys.executable, '-m', 'deployment.container', '--prepare'])
        while preparation.poll() is None and not stop.wait(.2):
            pass
        if stop.is_set():
            return 0
        if preparation.returncode:
            return preparation.returncode
        # Validate Web configuration before starting a Worker or listening.
        if role in {'all', 'web'}:
            from webui.wsgi import create_app
            create_app()
        if os.environ.get('BENCH_STORAGE_MODE') == 'ephemeral':
            print('试运行：容器重建会丢失进度、配置和结果，请及时导出。', flush=True)
        services = []
        if role in {'all', 'worker'}:
            services.append(launch([sys.executable, '-m', 'webui.worker']))
        if role in {'all', 'web'}:
            services.append(launch([sys.executable, '-m', 'gunicorn', '--bind', f'0.0.0.0:{port}',
                                    '--workers', '1', '--threads', '4', '--timeout', '120',
                                    '--access-logfile', '-', 'webui.wsgi:create_app()']))
        while not stop.wait(.2):
            for process in services:
                if process.poll() is not None:
                    print('Web 或 Worker 已退出；停止本容器其余进程。', flush=True)
                    return process.returncode or 1
        return 0
    finally:
        from local import descendants
        tracked = set()
        for process in children:
            if process.poll() is None:
                try:
                    tracked.update(descendants(process.pid))
                except (OSError, subprocess.CalledProcessError):
                    print('无法读取进程树；仍向服务进程组发送停止信号。', flush=True)
        # Let Worker cancel its current job before forcing termination. LCR and
        # the command runner create separate sessions, so also track descendants.
        for process in children:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 25
        for process in children:
            try:
                process.wait(timeout=max(.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        for process in children:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for pid in sorted(tracked, reverse=True):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for process in children:
            process.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    raise SystemExit(main())
