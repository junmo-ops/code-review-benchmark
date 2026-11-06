"""Restore the approved offline dependency file; no network or third-party modules."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parent
EXPECTED = '6d24a25a186101accaa0ec9993fcab7b45f3c79990506bcba2e13aba45756939'


def digest(file):
    result = hashlib.sha256()
    with file.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def main():
    manifest = json.loads((ROOT / 'TRANSFER_MANIFEST.json').read_text(encoding='utf-8'))
    target = ROOT / 'deployment/offline/dsh-npm.tar.gz'
    if target.exists():
        if target.is_symlink() or digest(target) != EXPECTED:
            raise ValueError('Existing dependency file is different; not overwritten')
        print('DSH dependency file is already verified: ' + str(target))
        return
    parts = []
    for item in manifest['parts']:
        name = item['path']
        if not name.startswith('deployment/offline/dsh/transfer-part') or not name[len('deployment/offline/dsh/transfer-part'):].isdigit():
            raise ValueError('Invalid part path')
        file = ROOT / name
        if file.is_symlink() or not file.is_file() or file.stat().st_size != item['bytes']:
            raise ValueError('Missing or invalid dependency part: ' + name)
        if digest(file) != item['sha256']:
            raise ValueError('Dependency part SHA256 mismatch: ' + name)
        parts.append(file)
    with tempfile.TemporaryDirectory(prefix='.restore-dsh-', dir=target.parent) as directory:
        pending = Path(directory) / 'npm.tar.gz'
        with pending.open('xb') as output:
            for file in parts:
                with file.open('rb') as source:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
        if digest(pending) != EXPECTED:
            raise ValueError('Restored dependency SHA256 mismatch; file not published')
        os.link(pending, target)
    print('DSH dependency file restored and verified: ' + str(target))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit('Restore failed: ' + str(error)) from None
