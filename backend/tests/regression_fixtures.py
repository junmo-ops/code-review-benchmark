"""Local resources for orchestration tests, never a real LCR runtime."""
import shutil
from unittest.mock import patch

from runner.paths import PROJECT_ROOT


def seed_datasets(root):
    target = root / 'data'
    target.mkdir(exist_ok=True)
    for source in (PROJECT_ROOT / 'data').glob('*.jsonl'):
        shutil.copyfile(source, target / source.name)


def simulated_lcr(test):
    fixture = patch('reviewers.lcr.adapter.tool_identity',
                    return_value={'test-fixture-lcr': 'orchestration-only-v1'})
    test.addCleanup(fixture.stop)
    fixture.start()


def frontend_environment():
    return {'BENCH_FRONTEND_DIST': str(PROJECT_ROOT / 'frontend/dist')}
