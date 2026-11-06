"""Explicit POD execution with native DSH and loopback model/storage substitutes."""
import json
import ctypes.util
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dsh_probe import case
from reviewers.dsh_skill import runtime, supervisor
from test_dsh_deployment import cli
from test_skill_assets import package
from tools.dsh_local_model import serve
from webui import skill_assets


class PodRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            'BENCH_DSH_RUNTIME_MODE': 'pod', 'BENCH_DSH_ENABLED': '1',
            'BENCH_CASE_STORAGE_MODE': 'local', 'BENCH_RUN_STORAGE_MODE': 'local',
            'BENCH_MODEL_STORAGE_MODE': 'local',
        }))

    def test_mode_is_explicit_and_never_masquerades_as_os_isolation(self):
        policy = runtime.policy('baseline')
        self.assertEqual(policy['runtime_mode'], 'pod')
        self.assertFalse(policy['os_isolation'])
        self.assertNotEqual(policy['isolation'], 'bubblewrap_pid_mount_network')
        with patch.dict(os.environ, BENCH_DSH_RUNTIME_MODE='typo'):
            with self.assertRaisesRegex(ValueError, 'BENCH_DSH_RUNTIME_MODE'):
                runtime.policy('baseline')

    def test_preflight_needs_no_bubblewrap_and_does_not_claim_namespace_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            binaries = Path(directory)
            for name in ('node', 'ps'):
                (binaries / name).symlink_to(shutil.which(name))
            result = cli('deployment.dsh_preflight', '--check-runtime',
                         env=dict(os.environ, PATH=str(binaries)))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            record = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(record['runtime_mode'], 'pod')
            self.assertFalse(record['os_isolation'])
            self.assertEqual(record['runtime_namespace'], 'not_applicable')
            self.assertEqual(record['status'], 'ready')

    def test_native_baseline_keeps_read_scope_and_records_mode_and_recovery(self):
        with serve(self.read_then_finish) as model, case(model) as probe:
            result = probe.review()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            cfg = json.loads((probe.run / 'run-config.json').read_text())
            self.assertEqual(cfg['dsh_policy']['runtime_mode'], 'pod')
            self.assertFalse(cfg['dsh_policy']['os_isolation'])
            target = probe.run / 'results/local.case.json'
            answer = json.loads(target.read_text())
            self.assertEqual(answer['dsh']['runtime_mode'], 'pod')
            self.assertFalse(answer['dsh']['os_isolation'])
            outcomes = json.dumps(model.requests[-1])
            self.assertIn('value = 2', outcomes)
            self.assertIn('BENCH_DSH_SCOPE_DENIED', outcomes)
            self.assertNotIn('local-secret', outcomes)
            saved = target.read_bytes()
            target.unlink()
            result = probe.stage('recover', '--instance', 'local.case')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(target.read_bytes(), saved)
            self.assertEqual(len(model.requests), 2)
            self.assertIn('无操作系统级沙盒', (probe.run / 'run-summary.zh-CN.md').read_text())

    @staticmethod
    def read_then_finish(body, index):
        if index != 1:
            return dict(content='{"status":"complete","comments":[]}'), 'stop'
        calls = [('read', dict(file_path='sample.py')),
                 ('read', dict(file_path='/etc/os-release')),
                 ('write', dict(file_path='sample.py', content='must-not-write'))]
        return dict(content=None, tool_calls=[dict(id=str(i), type='function',
                    function=dict(name=name, arguments=json.dumps(args)))
                    for i, (name, args) in enumerate(calls)]), 'tool_calls'

    def test_native_skill_uses_actual_bound_resource_path_and_survives_temp_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'skill.zip'
            archive.write_bytes(package())
            record = skill_assets.install(SimpleNamespace(data=root), archive, lambda *args: None)
            with serve(self.skill_resource) as model, case(model, skill_version=record['skill_version'],
                                                         assets_root=root) as probe:
                result = probe.review()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                target = probe.run / 'results/local.case.json'
                self.assertTrue(json.loads(target.read_text())['skill']['loaded'])
                self.assertIn('original', json.dumps(model.requests[-1]))
                saved = target.read_bytes()
                target.unlink()
                result = probe.stage('recover', '--instance', 'local.case')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(target.read_bytes(), saved)
                self.assertEqual(len(model.requests), 2)

    @staticmethod
    def skill_resource(body, index):
        if index != 1:
            return dict(content='{"status":"complete","comments":[]}'), 'stop'
        text = next(m['content'] for m in body['messages']
                    if m['role'] == 'user' and '<skill_resources>' in m.get('content', ''))
        base = next(line.removeprefix('Base directory for this skill: ')
                    for line in text.splitlines() if line.startswith('Base directory for this skill: '))
        return dict(content=None, tool_calls=[dict(id='rules', type='function',
                    function=dict(name='read', arguments=json.dumps(dict(
                        file_path=base + '/references/rules.txt'))))]), 'tool_calls'

    def test_runtime_mode_cannot_change_after_freezing(self):
        with serve() as model, case(model) as probe:
            probe.env['BENCH_DSH_RUNTIME_MODE'] = 'sandbox'
            result = probe.review()
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((probe.run / 'dsh-system-error.json').exists())
            self.assertEqual(model.requests, [])

    def test_supervisor_reclaims_detached_child_after_parent_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / 'repo').mkdir()
            script = ('import subprocess,sys; '
                      'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],'
                      'start_new_session=True); print(p.pid,flush=True)')
            evidence = work / 'case'
            pid = None
            try:
                supervisor.execute([sys.executable, '-c', script], work, evidence,
                                   time.monotonic() + 5, str)
                pid = int(supervisor.evidence_path(evidence, '.stdout.jsonl').read_text())
                self.assertFalse(supervisor._live(pid), f'detached child survived: {pid}')
            finally:
                if pid is not None:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_timeout_reclaims_detached_child_without_a_pid_namespace(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / 'repo').mkdir()
            script = ('import subprocess,sys,time; '
                      'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],'
                      'start_new_session=True); print(p.pid,flush=True); time.sleep(60)')
            evidence = work / 'case'
            with self.assertRaisesRegex(ValueError, '超时'):
                supervisor.execute([sys.executable, '-c', script], work, evidence,
                                   time.monotonic() + .5, str)
            child = int(supervisor.evidence_path(evidence, '.stdout.jsonl').read_text())
            record = json.loads(supervisor.evidence_path(evidence, '.execution.json').read_text())
            self.assertEqual(record['cleanup_status'], 'complete')
            self.assertFalse(supervisor._live(child))
            self.assertFalse(supervisor._live(record['pid']))

    def test_child_environment_does_not_inherit_platform_credentials_or_node_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / 'repo').mkdir()
            script = 'import os,json; print(json.dumps(dict(os.environ)))'
            with patch.dict(os.environ, {
                    'BENCH_CASE_S3_SECRET_ACCESS_KEY': 'must-not-inherit-cos',
                    'LCR_LLM_TOKEN': 'must-not-inherit-model',
                    'NODE_OPTIONS': '--require=must-not-load.js'}):
                supervisor.execute([sys.executable, '-c', script], work, work / 'case',
                                   time.monotonic() + 5, str)
            saved = supervisor.evidence_path(work / 'case', '.stdout.jsonl').read_text()
            self.assertNotIn('must-not-', saved)
            self.assertNotIn('NODE_OPTIONS', saved)
            self.assertEqual(json.loads(saved)['HOME'], str(work / 'home'))

    @unittest.skipUnless(ctypes.util.find_library('seccomp'), 'libseccomp is a local test-only fault injector')
    def test_native_pod_review_works_when_real_namespace_syscalls_are_denied(self):
        wrapper = Path(__file__).with_name('dsh_no_namespaces.py')
        with serve() as model, case(model, cli_entry=wrapper) as probe:
            self.assertIn('Test-only namespace denial: unshare', (probe.root / 'init.log').read_text())
            result = probe.review()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('Test-only namespace denial: unshare', result.stderr)
            answer = json.loads((probe.run / 'results/local.case.json').read_text())
            self.assertFalse(answer['dsh']['os_isolation'])
            self.assertEqual(answer['review']['status'], 'complete')
            self.assertIn('value = 2', json.dumps(model.requests[-1]))
            self.assertEqual(len(model.requests), 2)


if __name__ == '__main__':
    unittest.main()
