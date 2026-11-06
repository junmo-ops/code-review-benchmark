"""Opt-in acceptance of the real Node22.9-built artifact on the Node22.22 target."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from deployment.dsh_artifacts import digest
from deployment.dsh_npm import check_install
from deployment.dsh_release import restore
from deployment.dsh_release_validation import validate_package


@unittest.skipUnless(all(os.environ.get(name) for name in
                     ('DSH_CROSS_PACKAGE', 'DSH_BUILD_NODE', 'DSH_TARGET_NODE')), 'explicit real cross-built artifact')
class CrossBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = Path(os.environ['DSH_CROSS_PACKAGE']).resolve()
        cls.build_node = Path(os.environ['DSH_BUILD_NODE']).resolve()
        cls.target_node = Path(os.environ['DSH_TARGET_NODE']).resolve()
        cls.environment = dict(os.environ, PATH=str(cls.target_node.parent) + os.pathsep + os.environ['PATH'])

    def test_artifact_built_on_229_runs_real_target_checks_and_rejects_229_as_runtime(self):
        manifest = validate_package(self.package)
        self.assertEqual(manifest['toolchain']['node'], '22.9.0')
        self.assertEqual(manifest['runtimeValidation']['buildHostCheck'], 'not-run-cross-build')
        with tempfile.TemporaryDirectory(prefix='dsh-cross-positive-') as directory:
            root = Path(directory) / 'runtime'
            with patch.dict(os.environ, self.environment):
                result = restore(self.package, root)
            self.assertEqual(result['target_validation']['status'], 'passed')
            self.assertTrue(result['target_validation']['node'].startswith('22.22.'))
            self.assertEqual(len(result['target_validation']['components']), 5)
            rejected = subprocess.run([str(self.build_node), str(root / 'headless-target-check.mjs'), str(root)],
                                      capture_output=True, text=True, timeout=30)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('Target DSH runtime requires Node 22.22.x', rejected.stderr)
            old = dict(os.environ, PATH=str(self.build_node.parent) + os.pathsep + os.environ['PATH'])
            with patch.dict(os.environ, old):
                with self.assertRaisesRegex(ValueError, 'runtime requires Node 22.22.x'):
                    check_install(root)

    def test_bad_native_payload_cannot_be_installed_even_with_consistent_manifest_hashes(self):
        with tempfile.TemporaryDirectory(prefix='dsh-cross-invalid-') as directory:
            root = Path(directory)
            package = root / 'package'
            shutil.copytree(self.package, package)
            manifest_file = package / 'dist/runtime-manifest.json'
            manifest = json.loads(manifest_file.read_text())
            module = next(item for item in manifest['modules'] if item['name'] == '@deepseek-ai/node-addon-system-linux-x64')
            name = module['payload'] + '/bin/glibc/system.node'
            (package / name).write_bytes(b'not-a-native-addon')
            manifest['files'][name] = digest(package / name)
            manifest_file.write_text(json.dumps(manifest))
            validate_package(package)
            destination = root / 'runtime'
            with patch.dict(os.environ, self.environment):
                with self.assertRaises(ValueError):
                    restore(package, destination)
            self.assertFalse((destination / 'node_modules').exists())


if __name__ == '__main__':
    unittest.main()
