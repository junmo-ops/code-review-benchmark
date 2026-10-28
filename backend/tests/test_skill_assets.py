"""Skill assets through authenticated public resource APIs, no DSH or remote services."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import stat
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
import zipfile

from webui.service import Dashboard
from webui.wsgi import create_app


MAIN = b'---\nname: review-skill\ndescription: Inspect code changes\n---\nRead [rules](references/rules.txt).\n'


def package(entries=None, compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression) as archive:
        for name, content in (entries or [('SKILL.md', MAIN), ('references/rules.txt', b'original\r\n'),
                                         ('scripts/inspect.sh', b'exit 99\n')]):
            archive.writestr(name, content)
    return output.getvalue()


class SkillAssetApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.environment = patch.dict(os.environ, {
            'BENCH_DATA_ROOT': self.temp.name, 'BENCH_CONFIG_ROOT': self.temp.name,
            'BENCH_PUBLIC_URL': 'https://review.test', 'BENCH_PROXY_TOKEN': 'p' * 40,
            'BENCH_CSRF_TOKEN': 'c' * 40, 'BENCH_CASE_UPLOAD': '1',
            'BENCH_CASE_STORAGE_MODE': 'local',
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.dashboard = Dashboard(Path(self.temp.name))
        self.client = create_app(self.dashboard).test_client()
        self.headers = {'X-Bench-Proxy-Token': 'p' * 40, 'X-Bench-User': 'alice',
                        'X-Bench-Role': 'operator', 'X-Dashboard-Token': 'c' * 40,
                        'Origin': 'https://review.test'}

    def request(self, path, method='GET', **kwargs):
        return self.client.open(path, method=method, base_url='https://review.test',
                                headers=self.headers, **kwargs)

    def upload(self, data):
        reply = self.request('/api/uploads/start', 'POST', json=dict(
            kind='skill', filename='skill.zip', size=len(data), fingerprint=hashlib.sha256(data).hexdigest()))
        self.assertEqual(reply.status_code, 201, reply.json)
        identifier = reply.json['upload']['id']
        reply = self.request(f'/api/uploads/{identifier}/chunks/0', 'POST',
                             data=data, content_type='application/octet-stream')
        self.assertEqual(reply.status_code, 200, reply.json)
        reply = self.request(f'/api/uploads/{identifier}/complete', 'POST')
        self.assertEqual(reply.status_code, 202, reply.json)
        task = reply.json['task']
        deadline = time.monotonic() + 8
        while task['status'] in {'queued', 'running'} and time.monotonic() < deadline:
            time.sleep(.05)
            task = self.request('/api/cases/imports?id=' + task['id']).json['task']
        self.assertNotIn(task['status'], {'queued', 'running'}, task)
        return task

    def test_complete_package_is_available_and_original_bytes_are_traceable(self):
        original = package()
        self.assertEqual(self.upload(original)['status'], 'succeeded')
        listing = self.request('/api/skills')
        self.assertEqual(listing.status_code, 200)
        version = listing.json['versions'][0]
        detail = self.request('/api/skills/' + version['id']).json
        self.assertEqual(detail['name'], 'review-skill')
        self.assertEqual({item['path'] for item in detail['files']},
                         {'SKILL.md', 'references/rules.txt', 'scripts/inspect.sh'})
        self.assertEqual(detail['archive_sha256'], hashlib.sha256(original).hexdigest())
        self.assertNotEqual(detail['content_sha256'], detail['archive_sha256'])
        base = '/api/skills/' + version['id']
        self.assertEqual(self.request(base + '/archive').data, original)
        self.assertEqual(self.request(base + '/file?path=SKILL.md').data, MAIN)
        self.assertEqual(self.request(base + '/file?path=references/rules.txt').data, b'original\r\n')
        self.assertEqual(self.request(base + '/file?path=../manifest.json').status_code, 400)

    def test_changed_content_creates_version_and_repacked_duplicate_retains_both_archives(self):
        original = package()
        self.upload(original)
        first = self.request('/api/skills').json['versions'][0]
        repacked = package(compression=zipfile.ZIP_DEFLATED)
        self.assertEqual(self.upload(repacked)['status'], 'succeeded')
        versions = self.request('/api/skills').json['versions']
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]['id'], first['id'])
        detail = self.request('/api/skills/' + first['id']).json
        self.assertEqual(len(detail['archives']), 2)
        digest = hashlib.sha256(repacked).hexdigest()
        self.assertEqual(self.request('/api/skills/' + first['id'] + '/archive?sha256=' + digest).data, repacked)
        changed = package([('SKILL.md', MAIN), ('references/rules.txt', b'changed')])
        self.assertEqual(self.upload(changed)['status'], 'succeeded')
        self.assertEqual(len(self.request('/api/skills').json['versions']), 2)
        self.assertEqual(self.request('/api/skills/' + first['id'] + '/archive').data, original)

    def test_incomplete_package_is_not_published_and_can_be_followed_by_valid_import(self):
        task = self.upload(package([('SKILL.md', MAIN)]))
        self.assertEqual(task['status'], 'failed')
        self.assertIn('references/rules.txt', task['message'])
        self.assertEqual(self.request('/api/skills').json['versions'], [])
        self.assertEqual(self.upload(package())['status'], 'succeeded')

    def test_ambiguous_metadata_is_rejected_before_publication(self):
        main = b'---\nname: first\nname: second\ndescription: Duplicate metadata\n---\nReview.\n'
        self.assertEqual(self.upload(package([('SKILL.md', main)]))['status'], 'failed')
        self.assertEqual(self.request('/api/skills').json['versions'], [])

    def test_markdown_reference_forms_preserve_original_bytes(self):
        body = b'''Read [inline](references/rules.txt) and [reference][rules].
![image](references/image.png)
[![linked image](references/image.png)](references/rules.txt)
[shortcut] and ![picture][picture].
[space](references/space%20name.txt#section)
[paren](references/file\\(name\\).txt)
[external](https://example.invalid/missing) [anchor](#section)
`[not a resource](missing-inline.txt)`
```
![not a resource](missing-code.png)
```
<div>
[not markdown](missing-html.txt)
</div>

[rules]: references/rules.txt
[shortcut]: references/rules.txt
[picture]: references/image.png
'''
        main = MAIN + body
        entries = [('SKILL.md', main), ('references/rules.txt', b'rules'),
                   ('references/image.png', b'opaque image'),
                   ('references/space name.txt', b'space'), ('references/file(name).txt', b'paren'),
                   ('references/notes.MD', b'[relative](rules.txt)')]
        original = package([('review/' + name, data) for name, data in entries])
        self.assertEqual(self.upload(original)['status'], 'succeeded')
        version = self.request('/api/skills').json['versions'][0]
        base = '/api/skills/' + version['id']
        self.assertEqual(self.request(base + '/archive').data, original)
        self.assertEqual(self.request(base + '/file?path=review/SKILL.md').data, main)

    def test_missing_and_escaping_markdown_references_are_not_published(self):
        bodies = [b'[link](missing.txt)', b'![image](missing.png)',
                  b'[reference][r]\n\n[r]: missing.txt', b'![reference][r]\n\n[r]: missing.png',
                  b'[shortcut]\n\n[shortcut]: missing.txt',
                  b'[![nested](missing.png)](references/rules.txt)',
                  b'[escape](../../outside.txt)', b'[encoded](%2e%2e/outside.txt)',
                  b'[absolute](/review/references/rules.txt)']
        for body in bodies:
            with self.subTest(markdown=body):
                archive = package([('review/SKILL.md', MAIN + body),
                                   ('review/references/rules.txt', b'rules')])
                task = self.upload(archive)
                self.assertEqual(task['status'], 'failed')
                self.assertIn('本地引用资源', task['message'])
                self.assertEqual(self.request('/api/skills').json['versions'], [])
        archive = package([('review/SKILL.md', MAIN),
                           ('review/references/rules.txt', b'rules'),
                           ('review/references/notes.md', b'[escape](../../outside.txt)')])
        self.assertEqual(self.upload(archive)['status'], 'failed')

    def test_unmatched_brackets_complete_within_bounded_subprocess(self):
        archive = Path(self.temp.name) / 'brackets.zip'
        archive.write_bytes(package([('SKILL.md', MAIN + b'[' * 12800),
                                     ('references/rules.txt', b'rules')]))
        # A subprocess bounds the regression even if a future parser blocks its caller.
        script = ('from pathlib import Path; import sys; from webui.skill_package import unpack; '
                  'unpack(Path(sys.argv[1]), Path(sys.argv[2]), lambda *args: None)')
        result = subprocess.run([sys.executable, '-c', script, str(archive),
                                 str(Path(self.temp.name) / 'unpacked')],
                                capture_output=True, text=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unsafe_archives_are_rejected_without_publishing_versions(self):
        link = zipfile.ZipInfo('linked')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        fifo = zipfile.ZipInfo('pipe')
        fifo.external_attr = (stat.S_IFIFO | 0o600) << 16
        dangerous = [
            [('SKILL.md', MAIN), ('../escape', b'x')],
            [('SKILL.md', MAIN), ('/absolute', b'x')],
            [('SKILL.md', MAIN), ('C:/drive', b'x')],
            [('SKILL.md', MAIN), ('a\\b', b'x')],
            [('SKILL.md', MAIN), (link, b'/tmp/outside')],
            [('SKILL.md', MAIN), (fifo, b'')],
            [('SKILL.md', MAIN), ('same', b'1'), ('same', b'2')],
            [('SKILL.md', MAIN), ('Same', b'1'), ('same', b'2')],
            [('SKILL.md', MAIN), ('a', b'1'), ('a/b', b'2')],
            [('SKILL.md', MAIN), ('nested/SKILL.md', MAIN)],
            [('wrapper/SKILL.md', MAIN), ('outside.txt', b'x')],
            [('SKILL.md', b'not metadata')],
            [('SKILL.md', b'---\nname: test\n---\nReview')],
        ]
        for entries in dangerous:
            with self.subTest(names=[str(entry[0]) for entry in entries]):
                names = [entry[0] for entry in entries]
                if len(names) != len(set(names)):
                    with self.assertWarnsRegex(UserWarning, 'Duplicate name'):
                        archive = package(entries)
                else:
                    archive = package(entries)
                self.assertEqual(self.upload(archive)['status'], 'failed')
                self.assertEqual(self.request('/api/skills').json['versions'], [])
        self.assertFalse((Path(self.temp.name) / 'escape').exists())

    def test_limits_and_wrapped_package(self):
        with patch.dict(os.environ, {'BENCH_SKILL_UNPACK_MAX_MIB': '1'}):
            large = package([('SKILL.md', MAIN), ('payload', b'x' * (1024 ** 2))])
            self.assertEqual(self.upload(large)['status'], 'failed')
        entries = [('SKILL.md', MAIN)] + [(f'files/{index}', b'') for index in range(2000)]
        self.assertEqual(self.upload(package(entries))['status'], 'failed')
        wrapped = package([('review/SKILL.md', MAIN), ('review/references/rules.txt', b'wrapped')])
        self.assertEqual(self.upload(wrapped)['status'], 'succeeded')
        self.assertEqual(self.request('/api/skills').json['versions'][0]['main'], 'review/SKILL.md')

    def test_corrupted_version_is_unavailable_and_reimport_cannot_overwrite_it(self):
        original = package()
        self.upload(original)
        version = self.request('/api/skills').json['versions'][0]
        path = Path(self.temp.name) / 'skill-assets/versions' / version['id'] / 'content/references/rules.txt'
        path.write_bytes(b'tampered')
        listing = self.request('/api/skills').json
        self.assertEqual(listing['versions'], [])
        self.assertEqual(listing['unavailable'][0]['id'], version['id'])
        self.assertEqual(self.request('/api/skills/' + version['id']).status_code, 400)
        self.assertEqual(self.upload(original)['status'], 'failed')
        self.assertEqual(path.read_bytes(), b'tampered')

    def test_manifest_cannot_redirect_main_or_change_metadata_under_same_identity(self):
        self.upload(package())
        version = self.request('/api/skills').json['versions'][0]
        manifest = Path(self.temp.name) / 'skill-assets/versions' / version['id'] / 'manifest.json'
        original = json.loads(manifest.read_text())
        for changes in [{'main': 'scripts/inspect.sh'}, {'name': 'changed'}, {'description': 'changed'}]:
            with self.subTest(changes=changes):
                manifest.write_text(json.dumps(dict(original, **changes)))
                self.assertEqual(self.request('/api/skills').json['versions'], [])
                self.assertEqual(self.request('/api/skills/' + version['id']).status_code, 400)

    def test_cos_is_supported_and_viewer_cannot_import(self):
        with patch.dict(os.environ, {'BENCH_CASE_STORAGE_MODE': 'cos'}):
            listing = self.request('/api/skills').json
            self.assertTrue(listing['supported'])
            self.assertEqual(listing['storage_mode'], 'cos')
        self.headers['X-Bench-Role'] = 'viewer'
        self.assertEqual(self.request('/api/uploads/start', 'POST', json={}).status_code, 403)

    def test_disk_publish_failure_leaves_no_available_version_then_retry_succeeds(self):
        root = Path(self.temp.name) / 'skill-assets'
        root.mkdir()
        (root / 'versions').write_bytes(b'fault injection: directory replaced by file')
        self.assertEqual(self.upload(package())['status'], 'failed')
        self.assertEqual(self.request('/api/skills').json['versions'], [])
        (root / 'versions').unlink()
        self.assertEqual(self.upload(package())['status'], 'succeeded')
