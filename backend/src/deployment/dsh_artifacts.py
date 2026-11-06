"""Content inventory for controlled offline build inputs, not a delivery archive."""
import hashlib
import json
from pathlib import Path

from reviewers.dsh_skill.runtime import RUNTIME, VERSION

TARGET = dict(os='linux', cpu='x64', libc='glibc')


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def inputs():
    manifest = json.loads((RUNTIME / 'package.json').read_text())
    lock = json.loads((RUNTIME / 'package-lock.json').read_text())
    if manifest['dependencies'] != {'@deepseek-ai/dsh': VERSION}:
        raise ValueError('DSH version must remain pinned to ' + VERSION)
    if lock['packages']['']['dependencies'] != manifest['dependencies']:
        raise ValueError('DSH lock/manifest mismatch')
    if lock['packages']['node_modules/@deepseek-ai/dsh']['version'] != VERSION:
        raise ValueError('DSH locked version mismatch')
    return {name: digest(RUNTIME / name) for name in ('package.json', 'package-lock.json')}


def inventory(bundle, folder):
    root = bundle / folder
    if not root.is_dir() or root.is_symlink():
        raise ValueError('missing artifact directory: ' + str(root))
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError('non-regular artifact: ' + str(path))
        if path.is_file():
            files[path.relative_to(bundle).as_posix()] = digest(path)
    if not files:
        raise ValueError('empty artifact directory: ' + str(root))
    return files


def write_manifest(bundle, component, **extra):
    folder = 'npm-cache' if component == 'npm' else 'rpms'
    value = dict(schema=1, component=component, target=TARGET, inputs=inputs(),
                 files=inventory(bundle, folder), **extra)
    (bundle / f'{component}.json').write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    return value


def verify(bundle, component):
    path = bundle / f'{component}.json'
    if path.is_symlink():
        raise ValueError('symlink manifest: ' + str(path))
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('schema') != 1 or value.get('component') != component:
        raise ValueError('invalid artifact manifest: ' + str(path))
    if value.get('target') != TARGET:
        raise ValueError('artifact target mismatch: expected linux/x64/glibc')
    if value.get('inputs') != inputs():
        raise ValueError('DSH lock/manifest mismatch: ' + str(path))
    folder = 'npm-cache' if component == 'npm' else 'rpms'
    if value.get('files') != inventory(bundle, folder):
        raise ValueError('artifact digest mismatch or missing/unlisted files: ' + str(path))
    return value
