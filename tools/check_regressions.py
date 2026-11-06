"""Run offline release regressions without contacting models or COS."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    env = {name: value for name, value in os.environ.items()
           if not name.startswith(('BENCH_', 'LCR_', 'JUDGE_'))}
    env.update(BENCH_RUN_STORAGE_MODE='local', BENCH_CASE_STORAGE_MODE='local',
               BENCH_MODEL_STORAGE_MODE='local', PYTHONUTF8='1')
    paths = [str(ROOT / 'backend/src'), str(ROOT / 'backend/tests')]
    env['PYTHONPATH'] = os.pathsep.join(paths)
    tests = ['test_long_tasks', 'test_quality_flow', 'test_case_deletion', 'test_queue_recovery',
             'test_application_queries', 'test_http_contract', 'test_ccrab_dashboard',
             'test_worker_lifecycle', 'test_comparison_identity', 'test_submissions',
             'test_container.ContainerAccessTests.test_minimal_public_health_and_honest_storage_state',
             'test_container.ContainerAccessTests.test_csrf_origin_and_domain_still_apply',
             'test_container.ContainerAccessTests.test_missing_password_fails_closed']
    result = subprocess.run([sys.executable, '-m', 'unittest', *tests, '-v'], cwd=ROOT, env=env)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
