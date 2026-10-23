"""One verified npm-cache archive for Git-only build inputs, not a source ZIP."""
from contextlib import contextmanager
import gzip
import json
import os
from pathlib import Path, PurePosixPath
import tarfile
import tempfile

from .dsh_artifacts import digest, verify

MAX_BYTES = 1024 * 1024 * 1024
MAX_ENTRIES = 20000


def _check_member(member, names, total):
    path = PurePosixPath(member.name)
    allowed = member.name in {'npm.json', 'npm-cache', 'npm-cache/_cacache'} or member.name.startswith('npm-cache/_cacache/')
    if (not allowed or path.is_absolute() or '..' in path.parts
            or str(path) != member.name.rstrip('/') or member.name in names):
        raise ValueError('unsafe/duplicate npm archive path: ' + member.name)
    if not (member.isdir() or member.isfile()) or member.sparse is not None:
        raise ValueError('non-regular npm archive member: ' + member.name)
    if member.size < 0 or total + member.size > MAX_BYTES or len(names) >= MAX_ENTRIES:
        raise ValueError('npm archive exceeds extraction limits')
    names.add(member.name)
    return total + member.size


@contextmanager
def open_bundle(path):
    if not path.is_file():
        if path.name.endswith('.tar.gz'):
            raise ValueError('missing npm archive: ' + str(path))
        yield path
        return
    with tempfile.TemporaryDirectory(prefix='dsh-npm-unpack-') as directory:
        root = Path(directory)
        names, total = set(), 0
        with tarfile.open(path, 'r:gz') as archive:
            for member in archive:
                total = _check_member(member, names, total)
                archive.extract(member, root, filter='data')
        verify(root, 'npm')
        yield root


def _metadata(member):
    member.uid = member.gid = member.mtime = 0
    member.uname = member.gname = ''
    member.mode = 0o600
    return member


def pack(bundle, destination):
    manifest = verify(bundle, 'npm')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError('npm archive destination already exists; do not overwrite build inputs')
    with tempfile.TemporaryDirectory(prefix='.dsh-npm-pack-', dir=destination.parent) as directory:
        temporary = Path(directory) / 'npm.tar.gz'
        with temporary.open('wb') as output, gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as archive:
                for name in ['npm.json', *sorted(manifest['files'])]:
                    archive.add(bundle / name, arcname=name, recursive=False, filter=_metadata)
        with open_bundle(temporary):
            pass
        os.link(temporary, destination)
    print(json.dumps(dict(archive=str(destination), sha256=digest(destination),
                          component='npm', bytes=destination.stat().st_size)), flush=True)
