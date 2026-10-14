"""Exercise interrupted Git cleanup against actual temporary worktree metadata."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'backend/src'), str(ROOT)]
from runner.cloud import _clear_interrupted_workspaces
from runner.contracts import ReviewCase


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], stderr=subprocess.PIPE).decode().strip()


def prepare(root):
    source = root / 'repos/demo__repo'
    source.mkdir(parents=True)
    git(source, 'init', '-q')
    git(source, 'config', 'user.name', 'Acceptance')
    git(source, 'config', 'user.email', 'acceptance@example.invalid')
    (source / 'file.txt').write_text('real Git objects\n')
    git(source, 'add', '.')
    git(source, 'commit', '-qm', 'base')
    head = git(source, 'rev-parse', 'HEAD')
    run = root / 'batch'
    (run / 'workspaces').mkdir(parents=True)
    items = [ReviewCase(name, 'demo/repo', head, head)
             for name in ['existing-locked', 'missing-locked', 'completed-partial']]
    for item in items:
        workspace = run / 'workspaces' / item.safe_id
        git(source, 'worktree', 'add', '--detach', str(workspace), head)
        git(source, 'worktree', 'lock', str(workspace))
    shutil.rmtree(run / 'workspaces/missing-locked')
    partial = run / 'workspaces/completed-partial'
    git(source, 'worktree', 'unlock', str(partial))
    (partial / '.git').unlink()
    unregistered = run / 'workspaces/unregistered'
    unregistered.mkdir()
    (unregistered / 'leftover').write_text('partial files')
    items.append(ReviewCase('unregistered', 'demo/repo', head, head))
    items.append(ReviewCase('deleted-repository', 'demo/deleted', head, head))
    return source, run, items


def exercise(root):
    source, run, items = prepare(root)
    sibling = root / 'unrelated'
    git(source, 'worktree', 'add', '--detach', str(sibling), items[0].head_commit)
    git(source, 'worktree', 'lock', str(sibling))
    (sibling / 'keep.txt').write_text('must stay')
    (run / 'results').mkdir()
    answer = run / 'results/completed-partial.json'
    answer.write_text('saved answer evidence')
    before = answer.read_bytes()
    config = dict(repos_root=str(root / 'repos'))
    _clear_interrupted_workspaces(items, config, run)
    _clear_interrupted_workspaces(items, config, run)
    assert not list((run / 'workspaces').iterdir())
    assert answer.read_bytes() == before
    assert (sibling / 'keep.txt').read_text() == 'must stay'
    listing = git(source, 'worktree', 'list', '--porcelain')
    assert str(sibling) in listing
    assert all(str(run / 'workspaces' / i.safe_id) not in listing for i in items)
    outside = root / 'protected'
    outside.mkdir()
    (outside / 'keep').write_text('safe')
    (run / 'workspaces/linked').symlink_to(outside, target_is_directory=True)
    try:
        _clear_interrupted_workspaces([ReviewCase('linked', 'demo/repo', items[0].head_commit, items[0].head_commit)], config, run)
    except ValueError:
        pass  # The cleanup must reject links before touching their targets.
    else:
        raise AssertionError('symlink accepted')
    assert (outside / 'keep').read_text() == 'safe'
    return dict(status='passed', existingLocked=True, missingLocked=True, halfDeleted=True,
                unregisteredDirectory=True, idempotent=True, siblingPreserved=True,
                savedAnswerPreserved=True, symlinkRejected=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    target = args.output.resolve()
    if not target.is_relative_to(ROOT / 'output/playwright'):
        parser.error('输出只允许写入 output/playwright')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cleanup-probe-', dir=target.parent) as folder:
        result = exercise(Path(folder))
    target.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
