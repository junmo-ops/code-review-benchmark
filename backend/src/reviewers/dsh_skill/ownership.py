"""Interrupt a queue-owned CLI if its worker disappears; never replay paid work."""
from contextlib import contextmanager
import os
import signal
import threading

from .runtime import IsolationError


@contextmanager
def watch():
    value = os.getenv('BENCH_DSH_OWNER_PID')
    if value is None:
        yield
        return
    try:
        parent = int(value)
    except ValueError:
        raise IsolationError('DSH Worker 身份无效，拒绝启动') from None
    if parent != os.getppid():
        raise IsolationError('DSH Worker 已停止，拒绝启动')
    stopped = threading.Event()

    def observe():
        while not stopped.wait(.1):
            if os.getppid() != parent:
                os.kill(os.getpid(), signal.SIGINT)
                return

    thread = threading.Thread(target=observe, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=1)
