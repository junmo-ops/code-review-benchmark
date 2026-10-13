"""Low-volume container/process samples emitted to the platform log collector."""
from pathlib import Path
import os
import threading
import time
from runner.diagnostics import event, enabled


def _process(path):
    # /proc/stat contains CPU ticks and RSS without reading command-line secrets.
    try:
        text = (path / 'stat').read_text()
        end = text.rfind(')')
        fields = text[end + 2:].split()
        return dict(pid=int(path.name), ppid=int(fields[1]), role=_role(text[1:end]),
                    cpu_ticks=int(fields[11]) + int(fields[12]),
                    rss_bytes=max(0, int(fields[21])) * os.sysconf('SC_PAGE_SIZE'))
    except (OSError, ValueError, IndexError):
        return None


def _role(name):
    for prefix in ('lcr', 'git', 'gunicorn', 'python', 'node'):
        if name.startswith(prefix):
            return prefix
    return 'other'


def _descendants(rows, root):
    selected = {root}
    previous = set()
    while selected != previous:
        previous = selected.copy()
        selected.update(row['pid'] for row in rows if row['ppid'] in previous)
    return [row for row in rows if row['pid'] in selected]


def _cgroup():
    result = {}
    paths = {'memory_current': 'memory.current', 'memory_limit': 'memory.max',
             'memory_peak': 'memory.peak', 'memory_events': 'memory.events',
             'cpu': 'cpu.stat', 'pids_current': 'pids.current'}
    for key, name in paths.items():
        try:
            result[key] = (Path('/sys/fs/cgroup') / name).read_text().strip()[:1000]
        except OSError:
            continue
    if not result:
        for key, name in {'memory_current': 'memory.usage_in_bytes',
                          'memory_limit': 'memory.limit_in_bytes',
                          'memory_peak': 'memory.max_usage_in_bytes',
                          'memory_failures': 'memory.failcnt'}.items():
            try:
                result[key] = (Path('/sys/fs/cgroup/memory') / name).read_text().strip()[:1000]
            except OSError:
                continue
    return result


def sample():
    started = time.monotonic()
    rows = [row for path in Path('/proc').iterdir() if path.name.isdigit()
            if (row := _process(path)) is not None]
    selected = _descendants(rows, os.getpid())
    event('resources.sample', processes=sorted(selected, key=lambda row: row['rss_bytes'], reverse=True)[:12],
          process_count=len(selected), rss_total_bytes=sum(row['rss_bytes'] for row in selected),
          cpu_ticks=sum(row['cpu_ticks'] for row in selected), cgroup=_cgroup(),
          sample_seconds=round(time.monotonic() - started, 4))


def _interval():
    try:
        return min(60, max(1, float(os.environ.get('BENCH_DIAGNOSTICS_INTERVAL_SECONDS', '1'))))
    except ValueError:
        return 1


class ResourceMonitor:
    def __init__(self):
        self.stopping = threading.Event()
        self.interval = _interval()
        self.thread = threading.Thread(target=self._run, name='benchmark-diagnostics', daemon=True)

    def start(self):
        if enabled() and Path('/proc').is_dir():
            event('resources.start', interval_seconds=self.interval)
            self.thread.start()

    def _run(self):
        while not self.stopping.is_set():
            try:
                sample()
            except Exception as error:
                event('resources.failed', error_type=type(error).__name__)
            self.stopping.wait(self.interval)

    def stop(self):
        self.stopping.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
        event('resources.stop')
