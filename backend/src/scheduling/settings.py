"""Explicit opt-in; retain the existing single-instance deployment by default."""
import os
from runner.paths import PROJECT_ROOT
from .contracts import identifier, positive
from .config import cluster_options


def concurrency_limit():
    raw = os.environ.get('BENCH_GLOBAL_CONCURRENCY')
    if raw is None:
        return None
    try:
        return positive(int(raw))
    except ValueError as error:
        raise ValueError('BENCH_GLOBAL_CONCURRENCY 必须为 1–10000 的整数') from error


def settings(root=PROJECT_ROOT):
    value = cluster_options(root)
    if value.get('mode') not in {'single', 'mysql'}:
        raise ValueError('cluster.mode 须为 single 或 mysql')
    if value['mode'] == 'mysql':
        identifier(value.get('scope'), 64)
        # Execution capacity and leases are implementation details, not deployment knobs.
        value.update(pool='shared_gateway', worker_slots=5, lease_seconds=90)
        value.pop('model_pools', None)
    return value


def enabled(root=PROJECT_ROOT):
    return settings(root)['mode'] == 'mysql'


def services(root=PROJECT_ROOT):
    from .database import Database
    from .workflow import Workflow
    from .artifacts import Artifacts
    value = settings(root)
    from webui.case_storage import mode
    if mode() != 'cos':
        raise ValueError('多实例模式必须配置 COS，不能使用本地资源存储')
    database = Database.from_config(root=root)
    return Workflow(database, value['scope']), Artifacts(value['scope']), value


def runtime_key(reviewer):
    import hashlib
    from reviewers.registry import get_reviewer
    from .contracts import encode
    return hashlib.sha256(encode(get_reviewer(reviewer).tool_identity()).encode()).hexdigest()
