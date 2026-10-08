"""Windows bootstrap contracts checked without invoking Windows binaries or models."""
import builtins
import errno
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
import zipfile

from reviewers.lcr import adapter
from runner.cloud import exclusive
from tools import build_delivery, prepare_windows

class WindowsLocalTests(unittest.TestCase):
    def test_fresh_bootstrap_uses_current_python_then_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec_dir=root/'reviewers/lcr'; spec_dir.mkdir(parents=True)
            (spec_dir/'windows-runtime.json').write_text(json.dumps({'npm_package':'@scope/lcr','npm_version':'1.2.3','binary':'lcr-win32-x64.exe'}))
            with patch.object(prepare_windows, 'ROOT', root), patch.object(prepare_windows.platform, 'system', return_value='Windows'), patch.object(prepare_windows.platform, 'machine', return_value='AMD64'), patch.object(prepare_windows.shutil, 'which', return_value='present'), patch.object(prepare_windows.sys, 'version_info', (3,11)), patch.object(prepare_windows.sys, 'argv', ['prepare_windows.py']), patch.object(prepare_windows.subprocess, 'run') as run:
                prepare_windows.main()
            calls = [call.args[0] for call in run.call_args_list]
            self.assertEqual(calls[0][:3], [prepare_windows.sys.executable, '-m', 'venv'])
            self.assertIn('-r', calls[1])
            self.assertEqual(calls[2][1], 'install')
            self.assertEqual(calls[3][-1], '--restore-only')

    def test_windows_identity_freezes_actual_runtime_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package='.runtime/lcr/node_modules/@LV01.30/leancode-code-review-cli'
            files = [package+'/dist/lcr-win32-x64.exe', 'bin/lcr.cmd', package+'/package.json', package+'/bin/lcr.js', package+'/bin/version-check.js', 'reviewers/lcr/windows-runtime.json']
            for name in files:
                path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixture')
            (root/'local-runtime.json').write_text(json.dumps({'platform':'Windows','architecture':'AMD64','binary':'lcr-win32-x64.exe'}))
            with patch.object(adapter, 'ROOT', root), patch.object(adapter.platform, 'system', return_value='Windows'), patch.object(adapter.platform, 'machine', return_value='AMD64'):
                identity = adapter.tool_identity()
                self.assertEqual(set(identity), set(files+['local-runtime.json']))
                (root/package/'bin/lcr.js').write_bytes(b'changed')
                self.assertNotEqual(identity[package+'/bin/lcr.js'], adapter.tool_identity()[package+'/bin/lcr.js'])

    def test_batch_lock_rejects_duplicate_and_releases_after_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, 'abort'):
                with exclusive(root):
                    with self.assertRaises(ValueError):
                        with exclusive(root): pass
                    raise RuntimeError('abort')
            with exclusive(root): pass

    def test_windows_lock_imports_without_fcntl_and_locks_first_byte(self):
        windows_lock = MagicMock(LK_NBLCK=2, LK_UNLCK=0)
        original_import = builtins.__import__
        def windows_import(name, *args, **kwargs):
            if name == 'os': return SimpleNamespace(name='nt', SEEK_END=2)
            if name == 'msvcrt': return windows_lock
            if name == 'fcntl': raise ImportError('not available on Windows')
            return original_import(name, *args, **kwargs)
        namespace = {}
        source = (Path(__file__).resolve().parents[1]/'runner/locking.py').read_text()
        with patch('builtins.__import__', side_effect=windows_import): exec(compile(source,'locking.py','exec'),namespace)
        with tempfile.TemporaryFile('w+b') as lock:
            namespace['acquire'](lock)
            self.assertEqual(lock.tell(), 0)
            windows_lock.locking.assert_called_with(lock.fileno(), 2, 1)
            windows_lock.locking.side_effect = OSError(errno.EACCES, 'locked')
            with self.assertRaises(BlockingIOError): namespace['acquire'](lock)
            windows_lock.locking.side_effect = None
            namespace['release'](lock)
            windows_lock.locking.assert_called_with(lock.fileno(), 0, 1)

    def test_source_zip_contains_aacr_history_for_single_file_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'project'; root.mkdir()
            files = ['lcr/package.json','lcr/dist/lcr-linux-x64','lcr/bin/lcr.js','lcr/bin/version-check.js','bin/lcr.cmd','bundles/cline__cline.bundle']
            for name in files:
                path=root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(b'fixture')
            (root/'data').mkdir()
            (root/'data/pilot5.jsonl').write_text(json.dumps({'repo':'cline/cline'})+'\n')
            with patch.object(build_delivery,'ROOT',root): build_delivery.main()
            with zipfile.ZipFile(root.parent/'code-review-benchmark-source.zip') as archive:
                for name in files:
                    if name.startswith('bundles/') or name == 'bin/lcr.cmd': self.assertIn('project/'+name, archive.namelist())

if __name__ == '__main__': unittest.main()
