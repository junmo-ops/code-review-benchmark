"""Bounded case attempts with durable evidence, one deadline and no tool policy."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import time
from .storage import save


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def directory(run, case_id):
    if Path(case_id).name != case_id or case_id in {'.', '..'}:
        raise ValueError('案例证据标识无效')
    return run / 'execution-attempts' / case_id


def records(run, case_id):
    return [read(path) for path in sorted(directory(run, case_id).glob('*/attempt.json'))]


def evidence_files(run, case_id):
    raw = run / 'raw'
    return sorted(p for p in raw.iterdir() if p.name.startswith(case_id + '.') and p.is_file()) if raw.exists() else []


def request_records(run, case_id):
    """Read each attempt once; root raw files represent only the latest attempt."""
    attempts = records(run, case_id)
    if not attempts:
        paths = [run / 'raw' / (case_id + '.requests.json')]
    else:
        paths = [(directory(run, case_id) / row['id'] / 'raw' if row.get('archived') else run / 'raw')
                 / (case_id + '.requests.json') for row in attempts]
    return [record for path in paths if path.exists() for record in read(path)]


def totals(run, case_id):
    rows = records(run, case_id)
    return dict(attempt_count=len(rows),
                elapsed_seconds=round(sum(row.get('elapsed_seconds', 0) for row in rows), 3),
                accounting_complete=bool(rows) and all(row.get('archived', False) for row in rows),
                selected_attempt=next((row['id'] for row in rows if row['status'] == 'succeeded'), None))


def _archive(run, case_id, folder):
    destination = folder / 'raw'
    destination.mkdir()
    files = evidence_files(run, case_id)
    for path in files:
        shutil.copy2(path, destination / path.name)
    return files


def _attempt(run, case_id, number, deadline, execute, redact):
    folder = directory(run, case_id) / f'{number:04d}'
    folder.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    record = dict(id=folder.name, status='running', archived=False,
                  started_at=datetime.now(timezone.utc).isoformat(),
                  remaining_seconds=max(0, deadline-started))
    save(folder / 'attempt.json', record)
    try:
        result = execute(deadline)
        record['status'] = 'succeeded'
        return result
    except BaseException as error:
        record.update(status='failed', error_type=type(error).__name__, error=redact(str(error))[:2000])
        raise
    finally:
        record['elapsed_seconds'] = round(time.monotonic()-started, 3)
        # Never allow another attempt until this evidence has been archived.
        _archive(run, case_id, folder)
        record['archived'] = True
        save(folder / 'attempt.json', record)


def execute(run, case_id, *, budget_seconds, max_attempts, invoke, retryable, redact):
    """Invoke with the same monotonic deadline; exceptions remain authoritative."""
    if type(max_attempts) is not int or not 1 <= max_attempts <= 2 or budget_seconds <= 0:
        raise ValueError('案例执行预算无效')
    if records(run, case_id) or evidence_files(run, case_id):
        raise ValueError('案例已有执行证据；请恢复已有结果，重新检视须新建批次')
    deadline = time.monotonic() + budget_seconds
    for number in range(1, max_attempts + 1):
        try:
            return _attempt(run, case_id, number, deadline, invoke, redact)
        except Exception as error:
            if number == max_attempts or not retryable(error) or time.monotonic() >= deadline:
                raise
            # Every removed live file has an immutable archived copy above.
            for path in evidence_files(run, case_id):
                path.unlink()
