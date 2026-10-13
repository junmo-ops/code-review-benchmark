"""Protect resources referenced by unfinished benchmark jobs, not the entire pool."""
import json
from pathlib import Path

ACTIVE = {'queued', 'running', 'cancelling'}


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise ValueError('无法读取未结束测评的资源依赖，本次未删除；请检查批次记录') from None


def _spec(dashboard, job):
    dataset = job.get('resource_dataset')
    if dataset:
        return dataset
    if hasattr(dashboard, 'queue'):
        with dashboard.queue.connect() as db:
            row = db.execute('SELECT spec FROM jobs WHERE id=?', (job['id'],)).fetchone()
        value = json.loads(row['spec']) if row else None
        if isinstance(value, dict):
            return value.get('dataset')
    return None


def _dataset_dependencies(dashboard, identifier):
    from .case_assets import _registry
    row = next((item for item in _registry(dashboard.data)['datasets'] if item.get('id') == identifier), None)
    if row:
        return row.get('dataset_sha256'), set(row.get('repositories', []))
    path = (Path(dashboard.root) / 'data' / identifier).resolve()
    base = (Path(dashboard.root) / 'data').resolve()
    if not path.is_relative_to(base):
        raise ValueError('未结束测评的案例集路径无效，本次未删除')
    return None, _repositories(path)


def _repositories(path):
    try:
        rows = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
        return {row['repo'].replace('/', '__') for row in rows}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ValueError('无法确认未结束测评的仓库依赖，本次未删除；请检查案例清单') from None


def _dependencies(dashboard, job):
    key = job.get('key', '')
    folder = (Path(dashboard.data) / 'runs' / key).resolve()
    if not key or not folder.is_relative_to((Path(dashboard.data) / 'runs').resolve()):
        raise ValueError('未结束测评的批次路径无效，本次未删除')
    config = folder / 'run-config.json'
    if not config.is_file():
        config = folder / 'experiment.json'
    if config.is_file():
        frozen = _read(config)
        if frozen.get('repo'):
            return frozen.get('dataset_sha256'), {frozen['repo'].replace('/', '__')}
        return frozen.get('dataset_sha256'), _repositories(folder / 'dataset.jsonl')
    dataset = _spec(dashboard, job)
    if dataset:
        return _dataset_dependencies(dashboard, dataset)
    raise ValueError('未结束测评尚未记录资源依赖，本次未删除；请等待批次初始化完成')


def ensure_resource_unused(dashboard, *, slug=None, dataset_sha256=None):
    jobs = dashboard.queue.jobs() if hasattr(dashboard, 'queue') else dashboard.jobs
    for job in jobs.values():
        if job['status'] not in ACTIVE:
            continue
        digest, repositories = _dependencies(dashboard, job)
        if slug in repositories or (dataset_sha256 is not None and digest == dataset_sha256):
            raise ValueError(f'该资源被未结束的测评批次 {job["key"]} 使用；请等待任务结束或取消后再删除')
