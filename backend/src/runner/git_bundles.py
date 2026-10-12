"""Preserve locally available commit histories when exporting a repository."""
from pathlib import Path
import os
import subprocess
import tempfile


def git(path, *args, **options):
    result = subprocess.run(['git', '-C', str(path), *args], capture_output=True,
                            timeout=3600, env=dict(os.environ, GIT_NO_LAZY_FETCH="1"), **options)
    if result.returncode:
        raise ValueError('Git 历史处理失败：' + result.stderr.decode(errors='replace')[-1500:])
    return result.stdout


def _unreachable_roots(path, folder):
    listing = Path(folder) / 'unreachable.txt'
    with listing.open('wb') as output:
        result = subprocess.run(['git', '-C', str(path), 'fsck', '--full', '--unreachable', '--no-reflogs'],
                                stdout=output, stderr=subprocess.PIPE, timeout=3600,
                                env=dict(os.environ, GIT_NO_LAZY_FETCH="1"))
    if result.returncode:
        raise ValueError('仓库对象不完整：' + result.stderr.decode(errors='replace')[-1500:])
    with listing.open(encoding='utf-8', errors='replace') as source:
        return [parts[2] for line in source if len(parts := line.split()) == 3
                and parts[0] == 'unreachable' and parts[1] in {'commit', 'tag'}]


def create_complete_bundle(path, target):
    """Export branch histories and unreferenced commits without changing source refs."""
    path = Path(path).resolve()
    with tempfile.TemporaryDirectory(prefix='bundle-export-') as folder:
        orphaned = _unreachable_roots(path, folder)
        repo = Path(folder) / 'repository.git'
        repo.mkdir()
        git(repo, 'init', '--bare', '--quiet')
        objects = git(path, 'rev-parse', '--git-path', 'objects').decode().strip()
        objects = (path / objects).resolve()
        (repo / 'objects/info/alternates').write_text(objects.as_posix() + '\n', encoding='utf-8')
        refs = git(path, 'for-each-ref', '--format=%(objectname) %(refname)').decode().splitlines()
        updates = ['update ' + ref.split(' ', 1)[1] + ' ' + ref.split(' ', 1)[0] for ref in refs]
        head = git(path, 'rev-parse', 'HEAD').decode().strip()
        updates.append('update refs/bench-preserved/HEAD ' + head)
        updates.extend('update refs/bench-preserved/' + oid + ' ' + oid for oid in orphaned)
        git(repo, 'update-ref', '--stdin', input=('\n'.join(updates) + '\n').encode())
        git(repo, 'bundle', 'create', str(Path(target).resolve()), '--all')
