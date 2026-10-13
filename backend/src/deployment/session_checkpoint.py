"""Preserve resumable session records without configuration credentials."""
import json
import re
from reviewers.lcr.logs import redact

SENSITIVE = re.compile(r'(?:api[_-]?key|token|secret|password|authorization)$', re.I)


def clean(value, credentials=()):
    if isinstance(value, dict):
        return {key: '[REDACTED]' if SENSITIVE.search(key) else clean(item, credentials)
                for key, item in value.items()}
    if isinstance(value, list):
        return [clean(item, credentials) for item in value]
    if not isinstance(value, str):
        return value
    for secret in credentials:
        value = value.replace(secret, '[REDACTED]')
    return redact(value)


def credential_values(folder):
    values = set()
    for path in folder.glob('*.json'):
        try:
            if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
                continue
            document = json.loads(path.read_text(encoding='utf-8-sig'))
            _collect_credentials(document, values)
        except (OSError, ValueError):
            continue
    return values


def _collect_credentials(document, values):
    if not isinstance(document, dict):
        return
    for key, value in document.items():
        if SENSITIVE.search(key) and isinstance(value, str) and len(value) >= 8:
            values.add(value)
        elif isinstance(value, dict):
            _collect_credentials(value, values)


def sanitize_checkpoint(path, credentials=()):
    target = path.with_suffix('.sanitized')
    try:
        with path.open(encoding='utf-8-sig') as source, target.open('w', encoding='utf-8', newline='\n') as output:
            for line in source:
                if line.strip():
                    # Malformed records must not be published as a usable checkpoint.
                    output.write(json.dumps(clean(json.loads(line), credentials), ensure_ascii=False) + '\n')
        target.replace(path)
    finally:
        target.unlink(missing_ok=True)
