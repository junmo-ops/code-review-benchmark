"""Lease supervision. The supplied executor owns process cleanup and evidence uploads."""
from dataclasses import dataclass
import threading
from .contracts import LeaseLost, positive


@dataclass(frozen=True)
class Outcome:
    succeeded: bool
    evidence: dict


class LeaseGuard:
    def __init__(self, queue, lease, seconds=90):
        positive(seconds)
        if seconds < 6:
            raise ValueError('执行租约至少为 6 秒')
        self.queue, self.lease, self.seconds = queue, lease, seconds
        self.stopped = threading.Event()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._renew, name='case-lease-renew', daemon=True)

    def _renew(self):
        while not self.done.wait(self.seconds / 3):
            try:
                self.queue.heartbeat(self.lease, self.seconds)
            except Exception:
                # Any uncertain renewal stops work. A DB outage must never grant ownership.
                self.stopped.set()
                return

    def check(self):
        if self.stopped.is_set():
            raise LeaseLost('租约续期失败，当前尝试须停止')

    def checkpoint(self, record):
        self.check()
        try:
            self.queue.checkpoint(self.lease, record)
        except Exception:
            self.stopped.set()
            raise

    def __enter__(self):
        # Verify ownership before any remote or paid action, even if claim response was delayed.
        self.queue.heartbeat(self.lease, self.seconds)
        self.thread.start()
        return self

    def __exit__(self, kind, value, traceback):
        self.done.set()
        self.thread.join(timeout=16)
        self.stopped.set()


def execute_claim(queue, lease, execute, *, lease_seconds=90):
    """execute(lease, guard) must clean up children before returning an Outcome.

    Upload evidence under an immutable attempt key before returning. The SQL
    result commit is the publication point. Exceptions leave the lease to expire;
    they must not be converted to a successful empty answer.
    """
    with LeaseGuard(queue, lease, lease_seconds) as guard:
        outcome = execute(lease, guard)
        if not isinstance(outcome, Outcome):
            raise TypeError('案例执行器必须返回 Outcome')
        guard.check()
        queue.finish(lease, outcome.evidence, succeeded=outcome.succeeded)
    return outcome
