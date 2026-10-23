"""Real pinned Linux DSH, local HTTP model only. Run explicitly for DSH delivery."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from tools.dsh_local_model import serve

ROOT = Path(__file__).resolve().parents[2]


class LinuxRuntimeTests(unittest.TestCase):
    def test_freezes_real_runtime_and_requires_explicit_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            command = [str(ROOT / '.venv/bin/python'), '-m', 'runner.cloud', 'init',
                       '--run-id', 'baseline', '--runs-root', directory,
                       '--dataset', str(ROOT / 'data/pilot1.jsonl'), '--model', 'local-review',
                       '--reviewer', 'dsh_skill', '--skill-mode', 'baseline']
            env = dict(os.environ, PYTHONPATH=str(ROOT / 'backend/src'),
                       LCR_LLM_MODEL='local-review', LCR_LLM_BASE_URL='http://127.0.0.1:9/v1',
                       JUDGE_BASE_URL='http://127.0.0.1:9/v1', JUDGE_MODEL='local-judge')
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            cfg = json.loads((Path(directory) / 'baseline/run-config.json').read_text())
            self.assertEqual(cfg['skill_mode'], 'baseline')
            self.assertEqual(cfg['tool_hashes']['version'], '0.2.1-rc.2')
            self.assertEqual(cfg['max_case_retries'], 0)
            self.assertEqual(cfg['timeout_minutes'], 10)
            self.assertEqual(cfg['repository_mode'], 'head_snapshot_plus_merge_base_diff')
            self.assertEqual(cfg['dsh_policy']['request_retries'], 0)
            self.assertEqual(cfg['dsh_policy']['compaction_retries'], 0)
            self.assertEqual(cfg['dsh_policy']['overflow_retries'], 0)
            self.assertNotEqual(cfg['tool_hashes']['node_sha256'], '')

    def test_real_baseline_returns_complete_empty_answer_and_known_usage(self):
        empty = lambda body, index: (dict(content=json.dumps(dict(status='complete', comments=[]))), 'stop')
        with tempfile.TemporaryDirectory() as directory, serve(empty) as model:
            root = Path(directory)
            repo = root / 'repos/test__repo'
            repo.mkdir(parents=True)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args],
                                               stderr=subprocess.PIPE, text=True).strip()
            git('init', '-q')
            git('config', 'user.email', 'local@example.invalid')
            git('config', 'user.name', 'Local DSH probe')
            (repo / 'sample.py').write_text('value = 1\n')
            git('add', '.')
            git('commit', '-qm', 'base')
            base = git('rev-parse', 'HEAD')
            (repo / 'sample.py').write_text('value = 2\n')
            git('commit', '-qam', 'head')
            head = git('rev-parse', 'HEAD')
            dataset = root / 'dataset.jsonl'
            dataset.write_text(json.dumps(dict(instance_id='local-case', repo='test/repo',
                base_commit=base, head_commit=head, reference_comments=[])) + '\n')
            env = dict(os.environ, PYTHONPATH=str(ROOT / 'backend/src'),
                       LCR_LLM_MODEL='local-review', LCR_LLM_TOKEN='local-secret',
                       LCR_LLM_BASE_URL=model.url, JUDGE_MODEL='local-judge',
                       JUDGE_BASE_URL=model.url, JUDGE_API_KEY='judge-secret')
            common = [str(ROOT / '.venv/bin/python'), '-m', 'runner.cloud']
            suffix = ['--run-id', 'baseline', '--runs-root', str(root / 'runs')]
            commands = [
                common + ['init', *suffix, '--dataset', str(dataset), '--repos-root', str(root / 'repos'),
                          '--model', 'local-review', '--reviewer', 'dsh_skill', '--skill-mode', 'baseline'],
                common + ['review', *suffix, '--instance', 'local-case', '--external-timeout-seconds', '700'],
            ]
            for command in commands:
                result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=40)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run = root / 'runs/baseline'
            answer = json.loads((run / 'results/local-case.json').read_text())
            self.assertEqual(answer['review']['status'], 'complete')
            self.assertEqual(answer['review']['comments'], [])
            self.assertEqual(answer['skill'], {'mode': 'baseline', 'loaded': False})
            self.assertEqual(answer['review']['summary']['total_tokens'], 110)
            self.assertEqual(answer['review']['summary']['cache_read_tokens'], 90)
            self.assertEqual(answer['review']['usage_coverage'], {'known_requests': 1, 'requests': 1})
            self.assertEqual(len(model.requests), 1)
            self.assertTrue(list((run / 'raw').glob('*.session-*.jsonl')))
            saved = (run / 'results/local-case.json').read_bytes()
            recovered = subprocess.run(common + ['recover', *suffix, '--instance', 'local-case'],
                                       env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
            self.assertEqual((run / 'results/local-case.json').read_bytes(), saved)
            self.assertEqual(len(model.requests), 1)
            self.assertEqual((repo / 'sample.py').read_text(), 'value = 2\n')
