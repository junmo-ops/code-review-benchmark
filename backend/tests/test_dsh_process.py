"""Process contract probes, separate from the real-DSH model integration tests."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch
from reviewers.dsh_skill import runtime, supervisor


class ProcessBoundaryTests(unittest.TestCase):
    def test_timeout_reclaims_a_controlled_descendant_tree(self):
        marker = 'dsh-process-probe-' + uuid.uuid4().hex
        observed = set()
        stop = threading.Event()
        def observe():
            while not stop.wait(.05):
                rows = subprocess.check_output(['ps', '-eo', 'pid,args'], text=True).splitlines()
                for row in rows:
                    if marker in row and '/node' in row:
                        observed.add(int(row.split()[0]))
        code = f'''import {{spawn}} from 'node:child_process';
spawn('/node', ['-e', 'setInterval(() => {{}}, 1000)', '{marker}'], {{stdio:'inherit'}});
console.log('sensitive-output');
setInterval(() => {{}}, 1000);'''
        command = runtime.namespace() + ['--ro-bind', str(runtime.node()), '/node',
            '/node', '--input-type=module', '-e', code]
        thread = threading.Thread(target=observe, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                evidence = work / 'case.with.dots'
                with self.assertRaisesRegex(ValueError, '超时'):
                    supervisor.execute(command, work, evidence, time.monotonic() + 2,
                                       lambda text: text.replace('sensitive-output', '[REDACTED]'))
                record = json.loads(supervisor.evidence_path(evidence, '.execution.json').read_text())
                self.assertEqual(record['status'], 'timed_out')
                self.assertGreaterEqual(record['supervised_seconds'], 1.9)
                self.assertIn('cleanup_seconds', record)
                self.assertEqual(supervisor.evidence_path(evidence, '.stdout.jsonl').read_text(), '[REDACTED]\n')
        finally:
            stop.set()
            thread.join(timeout=2)
        self.assertGreaterEqual(len(observed), 2, 'parent and real descendant were not both observed')
        for pid in observed:
            path = Path(f'/proc/{pid}/stat')
            try:
                state = path.read_text().rsplit(')', 1)[1].split()[0]
            except FileNotFoundError:
                continue
            self.assertEqual(state, 'Z', f'live process remained: {pid}')

    def test_isolation_denies_real_writes_hidden_reads_and_network(self):
        runtime.probe()

    def test_cleanup_failure_keeps_execution_evidence_and_never_returns_success(self):
        scripts = [('pass', 'exited'), ('import time; time.sleep(30)', 'timed_out')]
        for script, expected in scripts:
            with self.subTest(status=expected), tempfile.TemporaryDirectory() as directory:
                self.check_cleanup_failure(Path(directory), script, expected)

    def check_cleanup_failure(self, work, script, expected):
        evidence = work / 'cleanup-failure'
        record_path = supervisor.evidence_path(evidence, '.execution.json')
        processes = []
        original = subprocess.Popen
        def launch(*args, **kwargs):
            process = original(*args, **kwargs)
            processes.append(process)
            return process
        try:
            with patch.object(subprocess, 'Popen', side_effect=launch), \
                 patch.object(os, 'killpg', side_effect=PermissionError('injected signal denial')):
                with self.assertRaisesRegex(PermissionError, 'injected signal denial'):
                    supervisor.execute([sys.executable, '-c', script],
                                       work, evidence, time.monotonic() + .2, str)
            record = json.loads(record_path.read_text())
            self.assertEqual(record['status'], expected)
            if expected == 'exited':
                self.assertEqual(record['exit_code'], 0)
            self.assertEqual(record['cleanup_status'], 'failed')
            self.assertIn('cleanup_seconds', record)
        finally:
            for process in processes:
                self.reap_injected_process(process)

    @staticmethod
    def reap_injected_process(process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=5)
