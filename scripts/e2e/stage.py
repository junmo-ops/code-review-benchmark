"""Real benchmark stages with deterministic replacements for paid LCR/judge calls."""
import json
import os
from pathlib import Path
import socket
import sys
import time
import subprocess

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'backend/src'), str(ROOT)]


def deny_network(*args, **kwargs):
    raise RuntimeError('Browser regression forbids network access from model stages')


def review(item, preview, run, diagnostic=False, resume_session_id=None):
    from reviewers.lcr import adapter
    from runner.storage import save
    if os.environ.get('E2E_VERIFY_WORKSPACE') == '1':
        actual = subprocess.check_output(['git', '-C', str(adapter.WORKER.repo), 'rev-parse', 'HEAD']).decode().strip()
        if actual != item.head_commit:
            raise ValueError('真实工作区提交与案例 head 不一致')
        save(run / 'raw' / (item.safe_id + '.workspace-evidence.json'),
             dict(actual_head=actual, expected_head=item.head_commit, workspace=str(adapter.WORKER.repo)))
    model = os.environ['LCR_LLM_MODEL']
    duration = 8 if 'slow' in model else .3
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        if adapter.cancellation_requested():
            raise ValueError('regression cancelled')
        time.sleep(.1)
    attempts = run / 'raw' / (item.safe_id + '.mock-attempts.json')
    count = json.loads(attempts.read_text()) if attempts.exists() else 0
    save(attempts, count + 1)
    if 'partial' in model and item.instance_id.endswith('fail') and not (run / 'mock-upstream-recovered').exists():
        raise ValueError('review_item_failed: deterministic regression failure')
    if 'retry' in model and count == 0:
        raise ValueError('504 Gateway Timeout: deterministic regression retry')
    raw = {'status': 'success', 'comments': [], 'summary': {'comments': 0}}
    folder = run / 'raw'
    save(folder / (item.safe_id + '.stdout.json'), raw)
    save(folder / (item.safe_id + '.execution.json'), {
        'status': 'exited', 'exit_code': 0, 'elapsed_seconds': duration,
        'command_without_credentials': ['regression', '--repo', str(adapter.WORKER.repo)],
    })
    print('MOCK LCR result:', item.instance_id, flush=True)
    return raw


def score(stage, suite, cases, run, config):
    from runner.storage import save, validate_answer
    included = [item.instance_id for item in cases
                if (run / 'results' / (item.safe_id + '.json')).is_file()
                and validate_answer(item, run / 'results' / (item.safe_id + '.json'))]
    if not included:
        raise ValueError('No complete answers to score')
    save(run / 'evaluation-scope.json', {'included': included, 'mock': True})
    save(run / 'metrics.json', {'status': 'complete', 'mock': True,
                              'summary': {'f1': 1, 'precision': 1, 'recall': 1}})
    (run / 'report.zh-CN.md').write_text('# 浏览器回归模拟报告\n\n模拟裁判结果，仅验证业务流程，不代表模型能力。\n')
    print('MOCK judge:', stage, included, flush=True)


def main():
    socket.socket.connect = deny_network
    from runner import cloud
    from reviewers.lcr import adapter
    from simulation import configure
    configure()
    adapter.run_lcr = review
    cloud.evaluators.execute = score
    stage, key, spec_text = sys.argv[1:]
    spec = json.loads(spec_text)
    sys.argv = ['regression', stage, '--run-id', key]
    if stage == 'init':
        sys.argv += ['--dataset', spec['dataset'], '--suite', spec['suite'],
                     '--evaluator', spec['evaluator'], '--repos-root', os.environ['BENCH_DATA_ROOT'] + '/repos',
                     '--model', os.environ['LCR_LLM_MODEL'], '--case-jobs', str(spec['case_jobs']),
                     '--timeout-minutes', str(spec['timeout_minutes']),
                     '--max-case-retries', str(spec['max_case_retries'])]
    elif stage == 'review':
        sys.argv += ['--external-timeout-seconds', str(spec['external_timeout_seconds'])]
    if spec.get('task'):
        sys.argv += ['--instance', spec['task']]
    if spec.get('retry'):
        sys.argv += ['--retry']
    cloud.main()


if __name__ == '__main__':
    main()
