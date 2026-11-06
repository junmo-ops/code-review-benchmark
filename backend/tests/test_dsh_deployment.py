"""Deployment command contracts; no model, COS or production container access."""
import os
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import shutil

ROOT = Path(__file__).resolve().parents[2]


def cli(module, *args, env=None):
    return subprocess.run([sys.executable, '-m', module, *map(str, args)], cwd=ROOT,
                          env=env or os.environ.copy(), capture_output=True, text=True, timeout=600)


class OfflineDeploymentTests(unittest.TestCase):
    def test_missing_bundle_fails_before_installing_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'runtime'
            result = cli('deployment.dsh_offline', 'install-npm', '--bundle', directory,
                         '--destination', destination)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('npm.json', result.stderr)
            self.assertFalse(destination.exists())

    def test_bundle_verifier_rejects_corrupt_bytes_and_changed_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            payload = bundle / 'npm-cache/_cacache/content-v2/payload'
            payload.parent.mkdir(parents=True)
            payload.write_bytes(b'known bytes')
            metadata = dict(schema=1, component='npm', target=dict(os='linux', cpu='x64', libc='glibc'),
                            inputs={name: hashlib.sha256((ROOT / 'dependencies/dsh' / name).read_bytes()).hexdigest()
                                    for name in ('package.json', 'package-lock.json')},
                            files={payload.relative_to(bundle).as_posix(): hashlib.sha256(b'known bytes').hexdigest()})
            (bundle / 'npm.json').write_text(json.dumps(metadata))
            payload.write_bytes(b'bad bytes')
            result = cli('deployment.dsh_offline', 'verify', '--bundle', bundle, '--component', 'npm')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('digest mismatch', result.stderr)
            payload.write_bytes(b'known bytes')
            metadata['inputs']['package-lock.json'] = '0' * 64
            (bundle / 'npm.json').write_text(json.dumps(metadata))
            result = cli('deployment.dsh_offline', 'verify', '--bundle', bundle, '--component', 'npm')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('lock/manifest mismatch', result.stderr)

    def test_wrong_target_and_malformed_manifests_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'npm.json'
            for value, message in [([], 'invalid artifact manifest'),
                                   (dict(schema=1, component='npm', target=dict(os='linux', cpu='arm64', libc='glibc')),
                                    'target mismatch')]:
                with self.subTest(message=message):
                    manifest.write_text(json.dumps(value))
                    result = cli('deployment.dsh_offline', 'verify', '--bundle', directory, '--component', 'npm')
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(message, result.stderr)

    def test_disabled_install_needs_no_artifacts_but_invalid_switch_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            for value, expected in [('0', 0), ('2', 2)]:
                with self.subTest(enabled=value):
                    env = dict(os.environ, BENCH_DSH_ENABLED=value, PATH='/nonexistent')
                    result = cli('deployment.dsh_offline', 'install', '--if-enabled', '--bundle', directory, env=env)
                    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    @unittest.skipUnless(os.environ.get('DSH_OFFLINE_PREPARE'), 'explicit networked npm preparation only')
    def test_prepare_creates_verifiable_locked_npm_inputs(self):
        bundle = os.environ['DSH_OFFLINE_PREPARE']
        result = cli('deployment.dsh_offline', 'prepare-npm', '--bundle', bundle)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout, flush=True)
        result = cli('deployment.dsh_offline', 'verify', '--bundle', bundle, '--component', 'npm')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_non_dsh_check_is_not_blocked_by_missing_runtime(self):
        env = dict(os.environ, BENCH_DSH_ENABLED='0', PATH='/nonexistent')
        result = cli('deployment.dsh_preflight', '--check-runtime', '--if-enabled', env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('disabled', result.stdout)

    def test_system_preparation_rejects_non_kylin_without_creating_rpms(self):
        if 'kylin' in Path('/etc/os-release').read_text().lower():
            self.skipTest('negative non-Kylin host contract')
        with tempfile.TemporaryDirectory() as directory:
            result = cli('deployment.dsh_offline', 'prepare-system', '--bundle', directory,
                         '--base-image', 'example.invalid/base@sha256:' + 'a' * 64,
                         '--rpm-source', 'approved-target-repository')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Kylin 10', result.stderr)
            self.assertFalse((Path(directory) / 'rpms').exists())

    def test_disabled_container_cannot_advertise_existing_local_dsh(self):
        env = dict(os.environ, BENCH_DSH_ENABLED='0')
        result = subprocess.run([sys.executable, '-c',
                                 'from reviewers.dsh_skill.runtime import identity; identity()'],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('disabled', result.stderr)

    def test_signed_rpm_bundle_can_move_from_preparation_to_build_context(self):
        with tempfile.TemporaryDirectory(prefix='dsh-rpm-boundary-fixture-') as directory:
            root = Path(directory)
            binaries = root / 'bin'
            binaries.mkdir()
            scripts = {
                'yumdownloader': 'for arg in "$@"; do case "$arg" in --destdir=*) dest=${arg#--destdir=};; esac; done\nprintf fixture > "$dest/bubblewrap.rpm"\n',
                'rpmkeys': 'printf "%s:\\n    Header V4 RSA/SHA256 Signature, key ID 01234567: OK\\n" "$3"\n',
                'rpm': 'case "$3" in "%{ARCH}") echo x86_64;; "%{NAME}"*) echo bubblewrap-0:fixture-1.x86_64;; *) echo "libc.so.6(GLIBC_2.17)(64bit)";; esac\n',
                'yum': 'printf installed > "$DSH_FIXTURE_INSTALL_MARKER"\n',
                'bwrap': 'echo "bubblewrap fixture"\n',
            }
            for name, script in scripts.items():
                path = binaries / name
                path.write_text('#!/bin/sh\n' + script)
                path.chmod(0o755)
            marker = root / 'installed'
            env = dict(os.environ, PATH=str(binaries), DSH_FIXTURE_INSTALL_MARKER=str(marker))
            # Simulate the external OS-release and RPM tools, never claim these are real Kylin RPMs.
            wrapper = ('import platform,sys; '
                       'platform.freedesktop_os_release=lambda:dict(ID="kylin",VERSION_ID="V10"); '
                       'from deployment.dsh_offline import main; raise SystemExit(main())')
            prepared = root / 'prepared'
            staged = root / 'checkout/deployment/offline/dsh'
            commands = [
                ['prepare-system', '--bundle', str(prepared), '--base-image',
                 'csbase.registry.cmbchina.cn/paas/cmb-python-3.12@sha256:' + 'a' * 64,
                 '--rpm-source', 'fixture-snapshot'],
                ['stage', '--bundle', str(prepared), '--destination', str(staged), '--component', 'system'],
                ['install-system', '--bundle', str(staged)],
            ]
            for command in commands:
                result = subprocess.run([sys.executable, '-c', wrapper, *command], env=env,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(marker.read_text(), 'installed')

    def test_build_check_does_not_certify_namespaces_and_runtime_failure_stops_prepare(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            shutil.copyfile(shutil.which('node'), path / 'node')
            (path / 'node').chmod(0o755)
            bwrap = path / 'bwrap'
            bwrap.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo "bubblewrap fixture"; exit 0; fi\necho "injected namespace denied" >&2\nexit 1\n')
            bwrap.chmod(0o755)
            env = dict(os.environ, BENCH_DSH_ENABLED='1', PATH=str(path))
            result = cli('deployment.dsh_preflight', '--check-build', env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('"runtime_namespace": "not_tested"', result.stdout)
            result = cli('deployment.container', '--prepare', env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('隔离不可用', result.stderr)
            self.assertNotIn('工作台运行目录已准备', result.stdout)

    @unittest.skipUnless(os.environ.get('DSH_OFFLINE_TEST_BUNDLE'), 'explicit prepared npm artifact integration')
    def test_missing_optional_grep_payload_is_not_silently_accepted_by_offline_install(self):
        with tempfile.TemporaryDirectory(prefix='dsh-missing-optional-') as directory:
            root = Path(directory)
            bundle = root / 'bundle'
            shutil.copytree(Path(os.environ['DSH_OFFLINE_TEST_BUNDLE']), bundle)
            lock = json.loads((ROOT / 'dependencies/dsh/package-lock.json').read_text())
            integrity = lock['packages']['node_modules/@vscode/ripgrep-linux-x64']['integrity']
            algorithm, encoded = integrity.split('-', 1)
            content_hash = base64.b64decode(encoded).hex()
            content = bundle / 'npm-cache/_cacache/content-v2' / algorithm / content_hash[:2] / content_hash[2:4] / content_hash[4:]
            content.unlink()
            manifest = json.loads((bundle / 'npm.json').read_text())
            del manifest['files'][content.relative_to(bundle).as_posix()]
            (bundle / 'npm.json').write_text(json.dumps(manifest))
            destination = root / 'runtime'
            command = ['bwrap', '--unshare-net', '--ro-bind', '/', '/', '--bind', directory, directory,
                       sys.executable, '-m', 'deployment.dsh_offline', 'install-npm',
                       '--bundle', str(bundle), '--destination', str(destination)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=180)
            print('Missing optional payload refusal: ' + result.stdout + result.stderr, flush=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('missing/wrong-version locked dependency: node_modules/@vscode/ripgrep-linux-x64', result.stderr)
            self.assertFalse((destination / 'node_modules').exists())

    @unittest.skipUnless(os.environ.get('DSH_OFFLINE_TEST_BUNDLE'), 'explicit prepared npm artifact integration')
    def test_real_install_is_offline_in_a_network_namespace_and_can_be_staged(self):
        bundle = Path(os.environ['DSH_OFFLINE_TEST_BUNDLE']).resolve()
        with tempfile.TemporaryDirectory(prefix='dsh-offline-contract-') as directory:
            root = Path(directory)
            staged = root / 'build-context/deployment/offline/dsh'
            result = cli('deployment.dsh_offline', 'stage', '--bundle', bundle, '--destination', staged,
                         '--component', 'npm')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('"complete": false', result.stdout)
            destination = root / 'runtime'
            command = ['bwrap', '--unshare-net', '--ro-bind', '/', '/', '--bind', directory, directory,
                       sys.executable, '-m', 'deployment.dsh_offline', 'install-npm',
                       '--bundle', str(staged), '--destination', str(destination)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print('Network-isolated real npm installation: ' + result.stdout, flush=True)
            result = subprocess.run(['node', str(destination / 'node_modules/@deepseek-ai/dsh/lib/bin.js'),
                                     '--version'], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), '0.2.1-rc.2')
            self.assertTrue((destination / 'offline-install.json').is_file())
            result = cli('deployment.dsh_offline', 'verify', '--bundle', staged, '--component', 'npm')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
