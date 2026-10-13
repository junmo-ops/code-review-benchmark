"""Preserve locally available commit histories when exporting a repository."""
from pathlib import Path
import os
import re
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
                                # fsck output is localized; its machine parser must use C.
                                env=dict(os.environ, GIT_NO_LAZY_FETCH="1",
                                         LC_ALL="C", LANG="C", LANGUAGE="C"))
    if result.returncode:
        raise ValueError('仓库对象不完整：' + result.stderr.decode(errors='replace')[-1500:])
    with listing.open(encoding='utf-8', errors='replace') as source:
        return _parse_unreachable_roots(source)


def _parse_unreachable_roots(source):
    roots = []
    for line in source:
        if not line.strip():
            continue
        match = re.fullmatch(r'unreachable (commit|tag|tree|blob) ([a-f0-9]{40}|[a-f0-9]{64})', line.strip())
        if not match:
            raise ValueError('无法解析 Git 对象检查结果，已停止导出以免遗漏提交：' + line.strip()[:200])
        if match[1] in {'commit', 'tag'}:
            roots.append(match[2])
    return roots


def _verify_preserved_roots(repo, target, roots):
    heads = git(repo, 'bundle', 'list-heads', str(target)).decode().splitlines()
    exported = {line.split()[0] for line in heads}
    missing = set(roots) - exported
    if missing:
        raise ValueError('Git bundle 导出遗漏本地历史：' + ', '.join(sorted(missing)[:5]))


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
        # Git expects LF; Windows text-mode CRLF can become part of the object path.
        (repo / 'objects/info/alternates').write_bytes((objects.as_posix() + '\n').encode('utf-8'))
        refs = git(path, 'for-each-ref', '--format=%(objectname)').decode().splitlines()
        head = git(path, 'rev-parse', 'HEAD').decode().strip()
        # Source refs can differ only by case (or conflict by path prefix).
        # Internal bundles preserve object histories; original names are not needed.
        roots = sorted(set(refs) | set(orphaned) | {head})
        updates = ['update refs/bench-preserved/' + oid + ' ' + oid for oid in roots]
        updates.append('update refs/heads/bench-snapshot ' + head)
        git(repo, 'update-ref', '--stdin', input=('\n'.join(updates) + '\n').encode())
        git(repo, 'symbolic-ref', 'HEAD', 'refs/heads/bench-snapshot')
        target = Path(target).resolve()
        git(repo, 'bundle', 'create', str(target), '--all')
        _verify_preserved_roots(repo, target, roots)
