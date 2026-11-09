"""Bounded run summaries for lists; execution validation always uses fresh data."""
from collections import OrderedDict
from copy import deepcopy
import datetime
from pathlib import Path
import threading
import time
from .files import read


def file_stamp(path):
    try:
        value = path.stat()
        return value.st_mtime_ns, value.st_size
    except FileNotFoundError:
        return None


def run_paths(data):
    root = Path(data) / 'runs'
    for pattern in ('*/run-config.json', 'mcr/*/run-config.json', 'ccrab/*/run-config.json'):
        yield from root.glob(pattern)
    for path in (root / 'ccrab').glob('*/config.json'):
        if not (path.parent / 'run-config.json').exists():
            yield path


def unreadable_run(key):
    return dict(key=key, name=key.split('/')[-1], suite='unknown', status='unreadable',
                error='批次数据不完整或格式不兼容', cases=[], artifacts=[], completed=0,
                total=0, metrics={}, created='', model='—', reviewer='—', evaluator='—')


class RunQueries:
    def __init__(self, context, *, maximum=4096, max_age=30):
        self.context = context
        self.maximum = maximum
        self.max_age = max_age
        self.cache = OrderedDict()
        self.index = {}
        self.lock = threading.RLock()

    def _stamp(self, folder, job):
        names = ('run-config.json', 'config.json', 'dataset.jsonl', 'evaluation-scope.json',
                 'results', 'metrics', 'metrics.json', 'status.json', 'raw', 'rounds', 'diagnostics')
        return tuple(file_stamp(folder / name) for name in names), job

    def _jobs(self):
        with self.context.lock:
            jobs = [dict(job) for job in self.context.jobs.values()]
        result = {}
        for job in sorted(jobs, key=lambda value: value.get('created_at', '')):
            key = job.get('key')
            if isinstance(key, str):
                result[key] = (job.get('id'), job.get('status'), job.get('completed_steps'),
                               job.get('ended_at'), job.get('profile'))
        return result

    def _created(self, key, cfg):
        folder = self.context.run_dir(key)
        path = folder / 'run-config.json'
        if not path.is_file():
            path = folder / 'config.json'
        return cfg.get('created_at') or datetime.datetime.fromtimestamp(
            path.stat().st_mtime, datetime.timezone.utc).isoformat()

    def _summary(self, key, folder, job):
        signature = self._stamp(folder, job)
        with self.lock:
            cached = self.cache.get(key)
            if cached and cached[0] == signature and time.monotonic() - cached[1] < self.max_age:
                self.cache.move_to_end(key)
                return deepcopy(cached[2])
        value = self._read_summary(key)
        if job and job[-1]:
            value['profile'] = job[-1]
        with self.lock:
            self.cache[key] = signature, time.monotonic(), value
            self.cache.move_to_end(key)
            while len(self.cache) > self.maximum:
                self.cache.popitem(last=False)
            return deepcopy(value)

    def _read_summary(self, key):
        cfg = self.context.config(key)
        if key.startswith('ccrab/'):
            return self._ccrab_summary(key, cfg)
        cases = self.context.cases(key)
        metrics = self.context.summary(key)
        status, scored = self.context._score_status(key, cases, metrics)
        suite = 'mcr' if key.startswith('mcr/') else cfg.get('suite', {}).get('id', 'aacr')
        return dict(key=key, name=key.split('/')[-1], suite=suite, model=cfg.get('review_model', '—'),
                    reviewer=cfg.get('reviewer', 'lcr'), evaluator='MCR 一对一' if suite == 'mcr' else cfg.get('evaluator', {}).get('id', 'original'),
                    judge_model=cfg.get('judge_model', '—'), created=self._created(key, cfg),
                    simulation=cfg.get('simulation', False),
                    status='simulation_complete' if cfg.get('simulation') and all(c['status'] == 'simulation_complete' for c in cases) else status,
                    total=len(cases), completed=sum(c['status'] == 'complete' for c in cases),
                    scored_cases=scored, metrics=metrics, cases=[], artifacts=[])

    def _ccrab_summary(self, key, cfg):
        path = self.context.run_dir(key) / 'status.json'
        result = read(path) if path.is_file() else {}
        status = result.get('status', 'pending')
        return dict(key=key, name=key.split('/')[-1], suite='ccrab', model=cfg.get('review_model', '—'),
                    reviewer='lcr', evaluator='发布测试（适配）', created=self._created(key, cfg),
                    status=status, total=1, completed=int(bool(result.get('review_executed'))),
                    metrics=self.context._ccrab_metrics(status, result), cases=[], artifacts=[])

    def all(self, *, details=False):
        root = self.context.data / 'runs'
        jobs = self._jobs()
        values = []
        for path in run_paths(self.context.data):
            key = path.parent.relative_to(root).as_posix()
            try:
                value = self.context.describe(key) if details else self._summary(key, path.parent, jobs.get(key))
            except (OSError, ValueError, KeyError, AttributeError, TypeError):
                value = unreadable_run(key)
            values.append(value)
        return sorted(values, key=lambda value: (value.get('created', ''), value['key']), reverse=True)

    def _labels(self):
        path = self.context.state / 'display-names.json'
        return read(path) if path.is_file() else {}

    def detail(self, key):
        self.context.run_dir(key)  # Validate the identifier even before creation finishes.
        try:
            value = self.context.describe(key)
        except FileNotFoundError:
            return None
        value['display_name'] = self._labels().get(key, value['name'])
        return value

    def page(self, page=1, page_size=10, suite='', search=''):
        if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError('分页参数无效，每页最多 100 条')
        names = self._labels()
        items = []
        for value in self._index():
            if (self.context.run_dir(value['key']) / 'comparison-protocol.json').is_file():
                continue
            value['display_name'] = names.get(value['key'], value['name'])
            if self._matches(value, suite, search):
                items.append(value)
        total = len(items)
        page = min(page, max(1, (total + page_size - 1) // page_size))
        start = (page - 1) * page_size
        jobs = self._jobs()
        selected = [self._page_summary(value, jobs) for value in items[start:start + page_size]]
        return dict(items=selected, total=total, page=page, page_size=page_size)

    def _page_summary(self, entry, jobs):
        key = entry['key']
        try:
            value = self._summary(key, self.context.run_dir(key), jobs.get(key))
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            value = unreadable_run(key)
        value['display_name'] = entry['display_name']
        return value

    def _index_entry(self, path, key):
        stamp = file_stamp(path)
        with self.lock:
            cached = self.index.get(key)
            if cached and cached[0] == stamp:
                return dict(cached[1])
        try:
            cfg = self.context.config(key)
            suite = key.split('/')[0] if '/' in key else cfg.get('suite', {}).get('id', 'aacr')
            value = dict(key=key, name=key.split('/')[-1], suite=suite,
                         model=cfg.get('review_model', '—'), created=self._created(key, cfg))
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            value = unreadable_run(key)
        with self.lock:
            self.index[key] = stamp, value
        return dict(value)

    def _index(self):
        # Only compact immutable metadata is indexed; case results are loaded after paging.
        root = self.context.data / 'runs'
        entries = [self._index_entry(path, path.parent.relative_to(root).as_posix())
                   for path in run_paths(self.context.data)]
        keys = {entry['key'] for entry in entries}
        with self.lock:
            for key in self.index.keys() - keys:
                del self.index[key]
        return sorted(entries, key=lambda value: (value.get('created', ''), value['key']), reverse=True)

    @staticmethod
    def _matches(value, suite, search):
        same_suite = not suite or value['suite'] == suite or (
            suite == 'swe_prbench_llm' and value['suite'] == 'swe_prbench')
        text = f"{value['display_name']} {value.get('model', '')} {value['suite']}"
        return same_suite and search.casefold() in text.casefold()
