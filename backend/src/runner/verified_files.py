"""Local verification receipts for immutable resource files; never skip changed files."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def _identity(path):
    stat = path.stat()
    return [str(path.resolve()), stat.st_dev, stat.st_ino, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns]


def verified_file(path, size, digest):
    path = Path(path)
    receipt = path.with_name(path.name + '.verified.json')
    try:
        before = _identity(path)
        if before[3] != size:
            return False
        try:
            saved = json.loads(receipt.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            saved = None
        if saved == {'identity': before, 'sha256': digest}:
            return _identity(path) == before
        checksum = hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                checksum.update(chunk)
        if checksum.hexdigest() != digest or _identity(path) != before:
            return False
        _remember(receipt, {'identity': before, 'sha256': digest})
        return True
    except OSError:
        return False


def _remember(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.verified-', delete=False) as output:
            temporary = Path(output.name)
            json.dump(value, output)
        os.replace(temporary, path)
    except OSError:
        # A missing receipt only costs a new hash on the next attempt.
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
