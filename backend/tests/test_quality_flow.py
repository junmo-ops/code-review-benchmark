"""Quality gate regressions using temporary files only."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QualityFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.check = load_tool('check_project')
        self.pack = load_tool('package_changes')
        folder = self.root / 'tools/quality'
        folder.mkdir(parents=True)
        self.baseline = folder / 'python-complexity-baseline.json'
        self.baseline.write_text('{"functions": {}}')

    def tearDown(self):
        self.temp.cleanup()

    def complex_file(self):
        path = self.root / 'tools/probe.py'
        path.write_text('def probe(value):\n' + ''.join(f'    if value == {n}:\n        value += 1\n' for n in range(16)))
        return path

    def test_no_git_directory_does_not_skip_new_complex_functions(self):
        path = self.complex_file()
        with patch.object(self.check, 'ROOT', self.root):
            self.assertTrue(self.check.check_python(path, False))
            self.assertTrue(self.check.check_python(path, True))

    def test_frozen_legacy_exception_expires_when_function_changes(self):
        path = self.complex_file()
        fingerprint = next(iter(self.check.functions(ast.parse(path.read_text()))))
        digest = hashlib.sha256(fingerprint.encode()).hexdigest()
        self.baseline.write_text(json.dumps({'functions': {'tools/probe.py': [digest]}}))
        with patch.object(self.check, 'ROOT', self.root):
            self.assertEqual(self.check.check_python(path, False), [])
            path.write_text(path.read_text().replace('value += 1', 'value += 2', 1))
            self.assertTrue(self.check.check_python(path, False))

    def test_archive_includes_new_files_deletions_and_executable_mode(self):
        hook = self.root / 'hook'
        hook.write_text('#!/bin/sh\nexit 0\n')
        hook.chmod(0o755)
        target = self.root / 'changes.zip'
        with patch.object(self.pack, 'ROOT', self.root):
            self.pack.build_archive(target, ['hook'], ['old.py'])
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(archive.read('hook'), hook.read_bytes())
            self.assertEqual(archive.read('DELETE_FILES.txt'), b'old.py\n')
            self.assertEqual(archive.getinfo('hook').external_attr >> 16 & 0o777, 0o755)

    def test_failed_archive_validation_does_not_publish_partial_zip(self):
        (self.root / 'source.py').write_text('value = 1\n')
        target = self.root / 'changes.zip'
        with patch.object(self.pack, 'ROOT', self.root), \
             patch.object(self.pack, 'verify_archive', side_effect=ValueError('bad archive')):
            with self.assertRaises(ValueError):
                self.pack.build_archive(target, ['source.py'], [])
        self.assertFalse(target.exists())
        self.assertFalse(target.with_suffix('.zip.part').exists())
