"""Tiny real Git/ZIP fixtures, confined to one regression run's directory."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import zipfile


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE).decode().strip()


def create(root):
    root = Path(root)
    repo = root / 'fixtures' / 'demo'
    repo.mkdir(parents=True)
    git(repo, 'init', '-q')
    git(repo, 'remote', 'add', 'origin', 'https://github.com/e2e/demo.git')
    git(repo, 'config', 'user.name', 'Browser Regression')
    git(repo, 'config', 'user.email', 'regression@example.invalid')
    (repo / 'sample.py').write_text('value = 1\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'base')
    base = git(repo, 'rev-parse', 'HEAD')
    (repo / 'sample.py').write_text('value = 2\n')
    git(repo, 'commit', '-qam', 'head')
    head = git(repo, 'rev-parse', 'HEAD')
    # Deliberately preserve an unreferenced commit in the source ZIP.
    git(repo, 'checkout', '--detach', base)
    (repo / 'orphan.py').write_text('orphan = True\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'unreferenced')
    orphan = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '--detach', head)
    archive = root / 'fixtures' / 'repository.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for path in repo.rglob('*'):
            if path.is_file():
                output.write(path, 'outer/demo/' + path.relative_to(repo).as_posix())
    shutil.copyfile(archive, root / 'fixtures/repository-copy.zip')
    rows = [dict(instance_id='demo-' + name, repo='e2e/demo', base_commit=base,
                 head_commit=commit, reference_comments=[])
            for name, commit in [('ok', head), ('fail', orphan)]]
    case_archive(root / 'fixtures/cases.zip', rows, '浏览器回归案例')
    many = [dict(rows[i % 2], instance_id=f'scale-{i:03d}') for i in range(50)]
    case_archive(root / 'fixtures/cases-50.zip', many, '浏览器回归50例')
    missing = [dict(rows[0], instance_id='demo-missing', head_commit='1' * 40)]
    case_archive(root / 'fixtures/missing.zip', missing, '缺少提交的案例')
    (root / 'fixtures/invalid.zip').write_bytes(b'not a zip')
    repository_update(repo, root / 'fixtures/repository-update.zip')
    size = int(os.environ.get('E2E_LARGE_REPOSITORY_MIB', '0'))
    if size:
        large_repository(root, repo, min(256, max(1, size)))


def case_archive(path, rows, name):
    data = ''.join(json.dumps(row) + '\n' for row in rows).encode()
    manifest = dict(format='code-review-benchmark-case-config-v1', suite='aacr', name=name, repositories=sorted({row['repo'].replace('/', '__') for row in rows}),
                    files={'dataset.jsonl': {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}})
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as output:
        output.writestr('manifest.json', json.dumps(manifest))
        output.writestr('dataset.jsonl', data)


def repository_update(repo, archive):
    git(repo, 'checkout', '-qb', 'next-version')
    (repo / 'version.py').write_text('version = 2\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'repository version update')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for path in repo.rglob('*'):
            if path.is_file():
                output.write(path, 'outer/demo/' + path.relative_to(repo).as_posix())


def large_repository(root, source, mib):
    repo = root / 'fixtures' / 'large-demo'
    shutil.copytree(source, repo)
    git(repo, 'remote', 'set-url', 'origin', 'https://github.com/e2e/large-demo.git')
    print(f'Preparing isolated large Git fixture: {mib} MiB', flush=True)
    with (repo / 'payload.bin').open('wb') as output:
        for _ in range(mib):
            output.write(os.urandom(1024 * 1024))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'large base')
    base = git(repo, 'rev-parse', 'HEAD')
    (repo / 'sample.py').write_text('value = 3\n')
    git(repo, 'commit', '-qam', 'large head')
    head = git(repo, 'rev-parse', 'HEAD')
    rows = [dict(instance_id=f'large-{i:03d}', repo='e2e/large-demo', base_commit=base,
                 head_commit=head, reference_comments=[]) for i in range(2)]
    case_archive(root / 'fixtures/large-cases.zip', rows, '浏览器回归大仓库')
    with zipfile.ZipFile(root / 'fixtures/large-repository.zip', 'w', zipfile.ZIP_STORED) as output:
        for path in repo.rglob('*'):
            if path.is_file():
                output.write(path, 'outer/large-demo/' + path.relative_to(repo).as_posix())
    print('Large Git fixture ready', flush=True)
