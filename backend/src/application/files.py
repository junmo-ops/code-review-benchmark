"""Atomic application metadata writes, separate from frozen scoring helpers."""
import datetime
import json
import os
import tempfile
import time
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        _replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _replace(source, destination):
    # Windows readers can briefly hold a handle that prevents atomic replacement.
    for attempt in range(6):
        try:
            source.replace(destination)
            return
        except PermissionError as error:
            if getattr(error, 'winerror', None) not in {5, 32, 33} or attempt == 5:
                raise
            time.sleep(.02 * (2 ** attempt))


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
