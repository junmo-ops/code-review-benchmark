"""Deployment paths; unset variables preserve the local layout."""
import os
from pathlib import Path

def data_root(root):
    return Path(os.environ.get('BENCH_DATA_ROOT', str(root))).expanduser().resolve()

def config_root(root):
    return Path(os.environ.get('BENCH_CONFIG_ROOT', str(root))).expanduser().resolve()

def docker_context():
    import sys
    return os.environ.get('BENCH_DOCKER_CONTEXT', 'colima-ccrab' if sys.platform=='darwin' else 'default')
