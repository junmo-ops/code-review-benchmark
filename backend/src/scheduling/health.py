"""Local EXEC probes. Dependency failures affect readiness, never directly liveness."""
import argparse
import os
from pathlib import Path
import time
from application.files import read, save
from runner.paths import PROJECT_ROOT
from runner.runtime import data_root

LIVE_SECONDS = 180
READY_SECONDS = 30


def directory():
    return data_root(PROJECT_ROOT) / 'worker-health'


class WorkerHealth:
    def __init__(self, owner):
        self.path = directory() / f'{os.getpid()}.json'
        self.value = dict(pid=os.getpid(), owner=owner, ready=False, stopped=False)

    def tick(self, *, ready=None):
        self.value.update(loop_at=time.monotonic())
        if ready is not None:
            self.value['ready'] = ready
        save(self.path, self.value)

    def close(self):
        self.value.update(stopped=True, ready=False)
        save(self.path, self.value)


def _alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = wintypes.DWORD()
        try:
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def check(kind, root=None, pid=None):
    records = []
    for path in Path(root or directory()).glob('*.json'):
        value = read(path)
        if (pid is None or value['pid'] == pid) and _alive(value['pid']):
            records.append(value)
    if not records:
        return False
    limit = LIVE_SECONDS if kind == 'live' else READY_SECONDS
    current = time.monotonic()
    return all(not value['stopped'] and 0 <= current - value['loop_at'] <= limit
               and (kind == 'live' or value['ready']) for value in records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('live', 'ready'))
    parser.add_argument('--pid', type=int, help='同一本地目录启动多个 Worker 时，可指定进程')
    args = parser.parse_args()
    try:
        ok = check(args.kind, pid=args.pid)
    except (OSError, ValueError, KeyError):
        ok = False
    print('worker ' + args.kind + ': ' + ('ok' if ok else 'unavailable'))
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
