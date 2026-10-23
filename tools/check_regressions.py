"""Run offline release regressions without contacting models or COS."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    env = dict(os.environ, BENCH_RUN_STORAGE_MODE='local', BENCH_CASE_STORAGE_MODE='local')
    paths = [str(ROOT / 'backend/src'), str(ROOT / 'backend/tests')]
    env['PYTHONPATH'] = os.pathsep.join(paths)
    tests = ['test_long_tasks', 'test_quality_flow', 'test_case_deletion', 'test_skill_assets',
             'test_container.ContainerAccessTests.test_minimal_public_health_and_honest_storage_state',
             'test_container.ContainerAccessTests.test_csrf_origin_and_domain_still_apply',
             'test_container.ContainerAccessTests.test_missing_password_fails_closed']
    result = subprocess.run([sys.executable, '-m', 'unittest', *tests, '-v'], cwd=ROOT, env=env)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
