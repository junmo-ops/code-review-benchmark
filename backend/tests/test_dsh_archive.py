"""Single npm build input: real archive parsing, corruption checks and offline install."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

from deployment.dsh_artifacts import write_manifest
from test_dsh_deployment import ROOT, cli


class NpmArchiveTests(unittest.TestCase):
    def bundle(self, root):
        bundle = root / 'prepared'
        cache = bundle / 'npm-cache/_cacache/content-v2/data'
        cache.parent.mkdir(parents=True)
        cache.write_bytes(b'controlled payload')
        write_manifest(bundle, 'npm')
        return bundle

    def test_single_archive_roundtrip_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self.bundle(root)
            archive = root / 'dsh-npm.tar.gz'
            result = cli('deployment.dsh_offline', 'pack-npm', '--bundle', bundle, '--destination', archive)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            first = archive.read_bytes()
            result = cli('deployment.dsh_offline', 'verify', '--bundle', archive, '--component', 'npm')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = cli('deployment.dsh_offline', 'pack-npm', '--bundle', bundle, '--destination', archive)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(archive.read_bytes(), first)
            (bundle / 'npm-cache/_cacache/content-v2/data').write_bytes(b'corrupt')
            result = cli('deployment.dsh_offline', 'pack-npm', '--bundle', bundle,
                         '--destination', root / 'bad.tar.gz')
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / 'bad.tar.gz').exists())

    def test_archive_rejects_paths_links_and_duplicate_members(self):
        cases = [('../outside', tarfile.REGTYPE), ('/etc/outside', tarfile.REGTYPE),
                 ('npm-cache/_cacache/link', tarfile.SYMTYPE), ('npm.json', tarfile.REGTYPE)]
        for name, kind in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                archive = Path(directory) / 'bad.tar.gz'
                with tarfile.open(archive, 'w:gz') as output:
                    item = tarfile.TarInfo(name)
                    item.type = kind
                    item.size = 1 if kind == tarfile.REGTYPE else 0
                    item.linkname = '/etc'
                    output.addfile(item, io.BytesIO(b'x'))
                    if name == 'npm.json':
                        output.addfile(item, io.BytesIO(b'x'))
                result = cli('deployment.dsh_offline', 'verify', '--bundle', archive, '--component', 'npm')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((Path(directory) / 'outside').exists())

    @unittest.skipUnless(os.environ.get('DSH_OFFLINE_TEST_BUNDLE'), 'explicit real offline npm bundle')
    def test_pod_install_from_single_archive_without_system_manifest_and_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'dsh-npm.tar.gz'
            result = cli('deployment.dsh_offline', 'pack-npm', '--bundle', os.environ['DSH_OFFLINE_TEST_BUNDLE'],
                         '--destination', archive)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            destination = root / 'runtime'
            command = ['bwrap', '--unshare-net', '--ro-bind', '/', '/', '--bind', directory, directory,
                       sys.executable, '-m', 'deployment.dsh_offline', 'install',
                       '--bundle', str(archive), '--destination', str(destination)]
            result = subprocess.run(command, cwd=ROOT,
                                    env=dict(os.environ, BENCH_DSH_RUNTIME_MODE='pod', TMPDIR=directory),
                                    capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            installed = json.loads((destination / 'offline-install.json').read_text())
            self.assertEqual(installed['version'], '0.2.1-rc.2')
            self.assertEqual(installed['locked_packages'], 615)


if __name__ == '__main__':
    unittest.main()
