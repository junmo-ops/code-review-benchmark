"""Frozen Skill snapshot invariance across POD and sandbox modes (no DSH binary).

Regression for the batch-kill: in POD mode the staged Skill snapshot shares the
host filesystem with untrusted execution (ADR-0002), and bash is not
path-scoped by the software guard, so a Skill's own Python scripts running from
inside the frozen snapshot could materialise ``scripts/__pycache__/*.pyc``
there.  The post-run integrity check then raised ``Skill 文件清单不一致`` and
stopped the whole batch after a completed review.

The fix has two layers:
  1. ``harden_snapshot`` removes write bits from the frozen snapshot in POD mode
     so any such write fails at the OS layer instead of mutating it;
  2. ``verify_after_execution`` fails the case (no quality answer) instead of
     stopping the batch when a mutation is still detected in POD mode; sandbox
     mode keeps the original fail-closed batch stop.
"""
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_skill_assets as assets
from webui import skill_assets
from reviewers.dsh_skill import selection
from reviewers.dsh_skill.runtime import IsolationError

IS_ROOT = hasattr(os, 'geteuid') and os.geteuid() == 0


def _install_version(root):
    blob = assets.package()
    archive = root / 'skill.zip'
    archive.write_bytes(blob)
    record = skill_assets.install(SimpleNamespace(data=root), archive, lambda *args: None)
    return record['skill_version']


def _bind(root, mode, run_name='run'):
    identifier = _install_version(root)
    run = root / run_name
    args = SimpleNamespace(skill_mode='skill', skill_version=identifier,
                           skill_identity_sha256=None)
    cfg = {'dsh_policy': {'runtime_mode': mode}}  # production: adapter.freeze()
    with patch.dict(os.environ, BENCH_DSH_RUNTIME_MODE=mode):
        selection.bind(args, cfg, run)
    return cfg, run, identifier


def _writable(tree):
    for path in (tree, *tree.rglob('*')):
        try:
            path.chmod(stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
        except OSError:
            pass


class FrozenSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dsh-frozen-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {
            'BENCH_DATA_ROOT': self.temp.name, 'BENCH_CONFIG_ROOT': self.temp.name,
            'BENCH_CASE_STORAGE_MODE': 'local'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_pod_bind_hardens_run_snapshot_and_verify_still_passes(self):
        cfg, run, identifier = _bind(self.root, 'pod')
        self.assertEqual(cfg['skill_mode'], 'skill')
        self.assertEqual(cfg['skill_version'], identifier)
        snapshot = run / 'skill'
        self._assert_hardened(snapshot)
        # Hardening must not break the frozen-content verification.
        selection.verify(cfg, run)

    def test_pod_stage_hardens_case_snapshot_and_integrity_stays_green(self):
        cfg, run, _ = _bind(self.root, 'pod')
        work = self.root / 'work'
        work.mkdir()
        selection.stage(cfg, run, work)
        self._assert_hardened(work / 'skill-version')
        selection.verify_after_execution(cfg, work)

    def test_sandbox_stage_stays_writable_and_mutation_stops_batch(self):
        cfg, run, _ = _bind(self.root, 'sandbox')
        work = self.root / 'work'
        work.mkdir()
        selection.stage(cfg, run, work)
        staged = work / 'skill-version'
        self._assert_writable(staged)  # bubblewrap supplies the boundary here
        (staged / 'content' / 'references' / 'rules.txt').write_bytes(b'tampered')
        with self.assertRaises(IsolationError):
            selection.verify_after_execution(cfg, work)

    def test_pod_mutation_fails_the_case_and_keeps_the_batch(self):
        cfg, run, _ = _bind(self.root, 'pod')
        work = self.root / 'work'
        work.mkdir()
        # A mutation that only occurs when the OS layer is defeated (e.g. the
        # pod runs as root): copy unconditionally, re-write and add files.
        staged = work / 'skill-version'
        shutil.copytree(run / 'skill', staged)
        _writable(staged)
        (staged / 'content' / 'references' / 'runtime-cache').mkdir()
        (staged / 'content' / 'references' / 'runtime-cache' / 'x.pyc').write_bytes(b'x')
        with self.assertRaises(ValueError) as captured:
            selection.verify_after_execution(cfg, work)
        message = str(captured.exception)
        self.assertIn('快照被改动', message)
        self.assertIn('不发布质量答卷', message)
        self.assertIn('批次继续', message)

    def test_intact_snapshot_passes_after_execution_in_both_modes(self):
        for mode in ('pod', 'sandbox'):
            with self.subTest(mode=mode):
                cfg, run, _ = _bind(self.root, mode, run_name='run-' + mode)
                work = self.root / ('work-' + mode)
                work.mkdir()
                selection.stage(cfg, run, work)
                selection.verify_after_execution(cfg, work)

    def test_harden_snapshot_rejects_symlinks(self):
        folder = self.root / 'linktree'
        folder.mkdir()
        (folder / 'file.txt').write_text('x')
        try:
            os.symlink(folder / 'file.txt', folder / 'alias.txt')
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links unavailable')
        with self.assertRaises(IsolationError):
            selection.harden_snapshot(folder)

    def _assert_hardened(self, tree):
        for path in tree.rglob('*'):
            if path.is_symlink():
                self.fail('symlink inside snapshot')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o222, 0,
                             f'{path} still writable')
        # Files must reject appends everywhere; directory-entry creation is
        # enforced by the OS on POSIX only (Windows ignores dir write bits).
        main = tree / 'content' / 'SKILL.md'
        if not IS_ROOT:
            with self.assertRaises((PermissionError, OSError)):
                with main.open('a'):
                    pass
        if os.name != 'nt' and not IS_ROOT:
            scripts = tree / 'content' / 'scripts'
            with self.assertRaises((PermissionError, OSError)):
                (scripts / '__pycache__').mkdir()

    def _assert_writable(self, tree):
        main = tree / 'content' / 'SKILL.md'
        with main.open('a'):
            pass
        (tree / 'content' / 'references' / 'extra.txt').write_text('x')


if __name__ == '__main__':
    unittest.main()
