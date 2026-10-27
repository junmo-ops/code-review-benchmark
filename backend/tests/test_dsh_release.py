"""Headless source, artifact and restore boundaries, with no network or native substitutes for acceptance."""
import io
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch

from deployment.dsh_artifacts import digest
from deployment.dsh_release import artifact_url, fetch, registry_url, restore, unpack
from deployment.dsh_release_validation import COMPILED, validate_installed, validate_package
from runner.paths import PROJECT_ROOT


def put(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return path


def fixture(root):
    modules = [dict(name='fixture', version='1', location='node_modules/fixture',
                    source='vendor/npm/a', payload='dist/packages/a')]
    native = [('node_modules/@deepseek-ai/node-addon-system-linux-x64', 'system'),
              ('node_modules/node-addon-require-builtin-linux-x64-gnu', 'builtin')]
    for location, slot in native:
        modules.append(dict(name=slot, version='1', location=location,
                            source='vendor/npm/' + slot, payload='dist/packages/' + slot))
        for name in sorted(COMPILED):
            if name.startswith(location + '/'):
                put(root, modules[-1]['payload'] + name[len(location):], 'native fixture, not executable')
    source = put(root, 'dist/packages/a/main.js', 'export const value = 1;\n')
    plan = dict(modules=modules, upstreamVersion='0.2.1-rc.2', capabilities=dict(headless=True),
                upstreamLockSha256=digest(PROJECT_ROOT / 'dependencies/dsh/package-lock.json'),
                files={'vendor/npm/a/main.js': digest(source)}, supplierFiles={})
    plan_path = put(root, 'dist/source-plan.json', json.dumps(plan))
    checker = put(root, 'dist/target-check.mjs', '// fixture checker, never native acceptance\n')
    expected = dict(name='fixture', version='internal1', sourcePlanSha256=digest(plan_path),
                    sourceFingerprint='a' * 64, generatedFiles={}, targetCheckSha256=digest(checker))
    manifest = dict(schema=1, kind='dsh-review-headless', upstreamVersion='0.2.1-rc.2',
                    internalVersion=expected['version'], sourcePlanSha256=expected['sourcePlanSha256'],
                    sourceFingerprint=expected['sourceFingerprint'], target=dict(os='linux', cpu='x64', libc='glibc'),
                    targetCheckSha256=expected['targetCheckSha256'],
                    runtimeValidation=dict(requiredNode='22.22.x', status='required-at-install'),
                    modules=modules, capabilities=plan['capabilities'], toolchain=dict(node='fixture'),
                    files={path.relative_to(root).as_posix(): digest(path)
                           for path in (root / 'dist/packages').rglob('*') if path.is_file()})
    put(root, 'dist/runtime-manifest.json', json.dumps(manifest))
    put(root, 'package.json', json.dumps(dict(name='fixture', version='internal1')))
    return expected, manifest


def registry_fixture():
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        data = json.dumps(dict(name='fixture', version='1.0.0')).encode()
        info = tarfile.TarInfo('package/package.json')
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    payload = output.getvalue()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            dist = dict(tarball=f'http://127.0.0.1:{self.server.server_port}/package.tgz',
                        integrity='sha512-' + base64.b64encode(hashlib.sha512(payload).digest()).decode())
            package = dict(name='fixture', version='1.0.0', dist=dist)
            value = dict(name='fixture', versions={'1.0.0': package})
            data = payload if self.path == '/package.tgz' else json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return ThreadingHTTPServer(('127.0.0.1', 0), Handler), requests


class SourceReleaseTests(unittest.TestCase):
    def test_malformed_payload_metadata_is_a_validation_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected, manifest = fixture(root)
            for field in ['files', 'toolchain', 'modules', 'runtimeValidation']:
                changed = dict(manifest, **{field: None})
                put(root, 'dist/runtime-manifest.json', json.dumps(changed))
                with self.assertRaises(ValueError):
                    validate_package(root, expected)

    def test_changed_target_checker_cannot_be_published_or_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected, _ = fixture(root)
            put(root, 'dist/target-check.mjs', '// bypass target check')
            with self.assertRaisesRegex(ValueError, 'mandatory target runtime'):
                validate_package(root, expected)

    def test_payload_cannot_rewrite_source_or_approve_its_own_source_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected, manifest = fixture(root)
            validate_package(root, expected)
            changed = put(root, 'dist/packages/a/main.js', 'rewritten core')
            manifest['files']['dist/packages/a/main.js'] = digest(changed)
            put(root, 'dist/runtime-manifest.json', json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'source/supplier'):
                validate_package(root, expected)
            put(root, 'dist/source-plan.json', '{}')
            with self.assertRaisesRegex(ValueError, 'unapproved'):
                validate_package(root, expected)

    def test_native_bytes_and_unlisted_files_are_also_hashed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected, _ = fixture(root)
            target = sorted((root / 'dist/packages').rglob('*.node'))[0]
            target.write_text('corrupt native')
            with self.assertRaisesRegex(ValueError, 'payload inventory'):
                validate_package(root, expected)
            put(root, 'dist/packages/a/extra.js', 'extra')
            with self.assertRaisesRegex(ValueError, 'payload inventory'):
                validate_package(root, expected)

    def test_dependencies_hooks_or_wrong_package_name_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected, _ = fixture(root)
            for extra in [dict(dependencies={'unknown': '1'}), dict(scripts={'postinstall': 'touch bad'}),
                          dict(name='wrong')]:
                put(root, 'package.json', json.dumps(dict(dict(name='fixture', version='internal1'), **extra)))
                with self.assertRaisesRegex(ValueError, 'identity or installation'):
                    validate_package(root, expected)

    def test_restore_has_no_partial_modules_on_failed_executable_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected, _ = fixture(root / 'package')
            destination = root / 'runtime'
            with patch('deployment.dsh_release.check_install', side_effect=ValueError('injected ABI failure')):
                with self.assertRaisesRegex(ValueError, 'ABI failure'):
                    restore(root / 'package', destination, expected)
            self.assertFalse((destination / 'node_modules').exists())
            with patch('deployment.dsh_release.check_install', return_value={'fixture': True}):
                restore(root / 'package', destination, expected)
            self.assertEqual(validate_installed(destination, expected)['locked_packages'], 3)
            with self.assertRaisesRegex(ValueError, 'refusing replacement'):
                restore(root / 'package', destination, expected)
            (destination / 'node_modules/fixture/main.js').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'installed headless inventory'):
                validate_installed(destination, expected)

    def test_tar_rejects_links_escapes_duplicates_and_special_entries(self):
        for name, kind in [('package/../../escape', tarfile.REGTYPE), ('package/link', tarfile.SYMTYPE),
                           ('package/fifo', tarfile.FIFOTYPE), ('other/file', tarfile.REGTYPE)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with tarfile.open(root / 'bad.tgz', 'w:gz') as archive:
                    info = tarfile.TarInfo(name)
                    info.type = kind
                    info.linkname = '/etc/passwd'
                    archive.addfile(info, io.BytesIO(b''))
                with self.assertRaises(ValueError):
                    unpack(root / 'bad.tgz', root / 'output')
                self.assertFalse((root / 'escape').exists())

    def test_registry_does_not_accept_embedded_credentials_or_local_sources(self):
        self.assertEqual(registry_url('http://registry.example/npm/'), 'http://registry.example/npm/')
        for url in ['file:///tmp', 'https://name:secret@registry.example/', 'https://registry.example/?token=secret']:
            with self.assertRaises(ValueError):
                registry_url(url)

    def test_metadata_cannot_redirect_archive_fetch_to_the_public_registry(self):
        registry = 'https://company.example/npm/'
        self.assertEqual(artifact_url(registry + 'file.tgz', registry), registry + 'file.tgz')
        metadata = dict(version='internal1', dist=dict(tarball='https://registry.npmjs.org/unapproved.tgz'))
        with tempfile.TemporaryDirectory() as directory:
            with patch('deployment.dsh_release.expected_release', return_value=dict(name='fixture', version='internal1')):
                with patch('deployment.dsh_release.npm_json', return_value=metadata) as npm:
                    with self.assertRaisesRegex(ValueError, 'outside'):
                        fetch(Path(directory), registry)
                    self.assertEqual(npm.call_count, 1)

    def test_archive_origin_normalizes_default_ports_without_accepting_other_origins(self):
        registry = 'http://central.jaf.cmbchina.cn/artifactory/api/npm/group-npm/'
        published = registry.replace('.cn/', '.cn:80/') + '@LV01.38/runtime.tgz'
        self.assertEqual(artifact_url(published, registry), published)
        for source, target in [('http://registry.example:80/file.tgz', 'http://REGISTRY.example/'),
                               ('https://registry.example:443/file.tgz', 'https://registry.example/'),
                               ('https://registry.example/file.tgz', 'https://registry.example:443/')]:
            with self.subTest(source=source, target=target):
                self.assertEqual(artifact_url(source, target), source)
        for source in ['http://central.jaf.cmbchina.cn:8080/file.tgz',
                       'http://central.jaf.cmbchina.cn:0/file.tgz',
                       'https://central.jaf.cmbchina.cn/file.tgz',
                       'http://central.jaf.cmbchina.cn.other.example/file.tgz',
                       'http://name:secret@central.jaf.cmbchina.cn/file.tgz']:
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    artifact_url(source, registry)

    def test_real_npm_fetches_only_the_checked_loopback_artifact_without_installing_dependencies(self):
        server, requests = registry_fixture()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                with patch('deployment.dsh_release.expected_release', return_value=dict(name='fixture', version='1.0.0')):
                    package = fetch(Path(directory), f'http://127.0.0.1:{server.server_port}/')
                self.assertEqual(json.loads((package / 'package.json').read_text())['version'], '1.0.0')
                self.assertFalse((package / 'node_modules').exists())
                self.assertIn('/package.tgz', requests)
                self.assertTrue(all(path in {'/fixture', '/package.tgz'} for path in requests), requests)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
