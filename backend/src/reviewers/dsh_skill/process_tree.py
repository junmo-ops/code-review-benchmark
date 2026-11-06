"""Reap POD-owned descendants, including children that create another session."""
from contextlib import contextmanager
import ctypes
import os


@contextmanager
def subreaper():
    from .runtime import IsolationError
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0:
        raise IsolationError('DSH POD 无法核验子进程回收能力')
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        raise IsolationError('DSH POD 无法启用子进程回收，拒绝运行')
    try:
        yield
    finally:
        if libc.prctl(36, previous.value, 0, 0, 0) != 0:
            raise IsolationError('DSH POD 无法恢复子进程回收状态')


def reap(pid):
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass
