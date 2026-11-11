"""Storage paths shared by the workbench, worker and local commands."""
import os
from pathlib import Path

LOCAL_DATA = (
    'runs', 'repos', 'ui-state', 'case-datasets', 'case-repositories',
    'case-packaging', 'git-import-cache', 'local-command-logs', 'zip-tool',
    'local-runtime.json',
)


def legacy_profiles(root):
    return sorted({*root.glob('model*.local.json'), *root.glob('model*.local.label'),
                   *root.glob('judge.local.json')})


def data_root(root):
    root = Path(root).resolve()
    override = os.environ.get('BENCH_DATA_ROOT')
    if override is not None:
        return Path(override).expanduser().resolve()
    if any((root / name).exists() for name in LOCAL_DATA):
        return root
    return root / 'runtime'


def config_root(root):
    root = Path(root).resolve()
    override = os.environ.get('BENCH_CONFIG_ROOT')
    if override is not None:
        return Path(override).expanduser().resolve()
    if 'BENCH_DATA_ROOT' not in os.environ and legacy_profiles(root):
        return root
    return data_root(root) / 'models'


def repository_root(root, frozen_path):
    """Resolve the former default repository path after local data migration."""
    path = Path(frozen_path)
    if path != root / 'repos' or path.exists() or 'BENCH_DATA_ROOT' in os.environ:
        return path
    return data_root(root) / 'repos'


def docker_context():
    import sys
    return os.environ.get('BENCH_DOCKER_CONTEXT', 'colima-ccrab' if sys.platform=='darwin' else 'default')
