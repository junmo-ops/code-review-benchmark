"""Nonblocking exclusive file locks for local Windows and POSIX runners."""
import errno
import os

if os.name == 'nt':
    import msvcrt
else:
    import fcntl

def acquire(lock):
    if os.name != 'nt':
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    lock.seek(0, os.SEEK_END)
    if lock.tell() == 0:
        lock.write(b'0')
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            raise BlockingIOError(error.errno, 'file is locked') from error
        raise

def release(lock):
    if os.name == 'nt':
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(lock, fcntl.LOCK_UN)
