"""Synchronous remote-submit barrier enabled only inside a leased cluster task."""
import os
from pathlib import Path
import time
import uuid


def checkpoint():
    location = os.environ.get('BENCH_CLUSTER_BARRIER')
    if not location:
        return
    path = Path(location)
    token = uuid.uuid4().hex
    path.write_text(token, encoding='ascii')
    acknowledgement = path.with_suffix('.ack')
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if acknowledgement.exists() and acknowledgement.read_text(encoding='ascii') == token:
            return
        time.sleep(.1)
    raise RuntimeError('远程任务检查点未能持久化，停止提交或轮询')
