"""Whole-case deadline and process-tree cleanup, including an exited group leader."""
import os
from contextlib import nullcontext
import signal
import subprocess
import time
from pathlib import Path
from datetime import datetime, timezone
from runner.storage import save
from local import descendants
from . import runtime
from .process_tree import subreaper, reap


def evidence_path(base, suffix):
    return Path(str(base) + suffix)


def execute(command, work, evidence, deadline, redact):
    with subreaper() if runtime.runtime_mode() == 'pod' else nullcontext():
        return _execute(command, work, evidence, deadline, redact)


def _execute(command, work, evidence, deadline, redact):
    started = time.monotonic()
    record = dict(status='starting', started_at=datetime.now(timezone.utc).isoformat(),
                  process_timeout_seconds=max(0, deadline - started),
                  runtime_mode=runtime.runtime_mode(), os_isolation=runtime.runtime_mode() == 'sandbox',
                  command_without_credentials=[redact(value) for value in command])
    save(evidence_path(evidence, '.execution.json'), record)
    process = None
    previous = set(descendants(os.getpid())) if runtime.runtime_mode() == 'pod' else None
    try:
        with (work / 'stdout').open('wb') as out, (work / 'stderr').open('wb') as err:
            process = subprocess.Popen(command, stdout=out, stderr=err, start_new_session=True,
                                       env=runtime.environment(work),
                                       cwd=work / 'repo' if runtime.runtime_mode() == 'pod' else None)
            process.dsh_previous_children = previous
            record.update(status='running', pid=process.pid)
            save(evidence_path(evidence, '.execution.json'), record)
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
            record.update(status='exited', exit_code=process.returncode)
    except subprocess.TimeoutExpired:
        record['status'] = 'timed_out'
        raise ValueError('DSH 整例检视超时，未生成完整答卷') from None
    except KeyboardInterrupt:
        record['status'] = 'interrupted'
        raise
    finally:
        record['supervised_seconds'] = round(time.monotonic() - started, 3)
        cleanup = time.monotonic()
        record['cleanup_status'] = 'failed'
        try:
            _terminate(process)
            record['cleanup_status'] = 'complete'
        finally:
            record['cleanup_seconds'] = round(time.monotonic() - cleanup, 3)
            _save_output(work, evidence, redact)
            save(evidence_path(evidence, '.execution.json'), record)
    return record


def _terminate(process):
    if process is None:
        return
    deadline = time.monotonic() + 10
    tracked = descendants(process.pid)
    previous = getattr(process, 'dsh_previous_children', None)
    if previous is not None:
        tracked.update(set(descendants(os.getpid())) - previous - {process.pid})
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _kill_descendants(tracked)
    process.wait(timeout=max(.001, deadline - time.monotonic()))
    if previous is not None:
        _reclaim_adopted(process.pid, previous, tracked, deadline)
    # Bubblewrap's inner session can outlive its outer process while the kernel
    # tears down the PID namespace. Cleanup is not complete until it stops.
    while any(_live(pid) for pid in tracked):
        if time.monotonic() >= deadline:
            raise ValueError('DSH 进程树清理超时，不能确认隔离进程已停止')
        time.sleep(.01)
    for pid in tracked:
        reap(pid)


def _kill_descendants(tracked):
    for pid in tracked:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _reclaim_adopted(parent, previous, tracked, deadline):
    while True:
        tracked.update(set(descendants(os.getpid())) - previous - {parent})
        _kill_descendants(tracked)
        for pid in tracked:
            reap(pid)
        remaining = set(descendants(os.getpid())) - previous - {parent}
        tracked.update(remaining)
        if not any(_live(pid) for pid in tracked):
            return
        if time.monotonic() >= deadline:
            raise ValueError('DSH POD 后代进程回收超时，停止批次')
        time.sleep(.01)


def _live(pid):
    try:
        state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
    except FileNotFoundError:
        return False
    return state not in {'Z', 'X'}


def _save_output(work, evidence, redact):
    for source, suffix in [('stdout', '.stdout.jsonl'), ('stderr', '.stderr.txt')]:
        path = work / source
        if path.exists():
            evidence_path(evidence, suffix).write_text(redact(path.read_text(errors='replace')), encoding='utf-8')
