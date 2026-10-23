"""Explicit Docker COPY/export proof only, not a Kylin image or namespace acceptance."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.environ.get('DSH_OFFLINE_CONTEXT'), 'explicit local Docker build-context export')
class OfflineBuildContextTests(unittest.TestCase):
    def test_staged_artifact_bytes_reach_root_context_without_host_materials(self):
        with tempfile.TemporaryDirectory(prefix='dsh-context-proof-') as directory:
            root = Path(directory)
            dockerfile = root / 'Dockerfile'
            dockerfile.write_text('FROM scratch\nCOPY . /context/\n')
            exported = root / 'exported'
            result = subprocess.run(['docker', 'build', '--network=none', '--no-cache', '--progress=plain',
                                     '--file', str(dockerfile), '--output', 'type=local,dest=' + str(exported),
                                     str(ROOT)], capture_output=True, text=True, timeout=180)
            print(result.stdout + result.stderr, flush=True)
            self.assertEqual(result.returncode, 0)
            context = exported / 'context'
            bundle = context / 'deployment/offline/dsh'
            result = subprocess.run([sys.executable, '-m', 'deployment.dsh_offline', 'verify',
                                     '--component', 'npm', '--bundle', str(bundle)],
                                    capture_output=True, text=True, timeout=30)
            print(result.stdout + result.stderr, flush=True)
            self.assertEqual(result.returncode, 0)
            expected = (ROOT / 'deployment/offline/dsh/npm.json').read_bytes()
            self.assertEqual(hashlib.sha256((bundle / 'npm.json').read_bytes()).hexdigest(),
                             hashlib.sha256(expected).hexdigest())
            archive = context / 'deployment/offline/dsh-npm.tar.gz'
            source = ROOT / 'deployment/offline/dsh-npm.tar.gz'
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),
                             hashlib.sha256(source.read_bytes()).hexdigest())
            result = subprocess.run([sys.executable, '-m', 'deployment.dsh_offline', 'verify',
                                     '--component', 'npm', '--bundle', str(archive)],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for name in ('.git', '.venv', '.scratch', 'output', 'backend/tests', 'scripts/e2e',
                         'frontend/node_modules', 'dependencies/dsh/node_modules'):
                self.assertFalse((context / name).exists(), name)
