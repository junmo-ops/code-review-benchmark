"""Explicit Docker COPY/export proof only, not a Kylin image or namespace acceptance."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.environ.get('DSH_RELEASE_CONTEXT'), 'explicit local Docker build-context export')
class SourceReleaseBuildContextTests(unittest.TestCase):
    def test_internal_release_inputs_reach_context_without_archives_or_source_producer(self):
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
            for name in ('dependencies/dsh/headless-release.json', 'dependencies/dsh/package-lock.json',
                         'backend/src/deployment/dsh_release.py', 'backend/src/deployment/dsh_release_validation.py'):
                self.assertEqual(hashlib.sha256((context / name).read_bytes()).hexdigest(),
                                 hashlib.sha256((ROOT / name).read_bytes()).hexdigest())
            for name in ('.git', '.venv', '.scratch', 'output', 'backend/tests', 'scripts/e2e',
                         'frontend/node_modules', 'dependencies/dsh/node_modules',
                         'dependencies/dsh-review-source', 'deployment/offline/dsh',
                         'deployment/offline/dsh-npm.tar.gz'):
                self.assertFalse((context / name).exists(), name)
