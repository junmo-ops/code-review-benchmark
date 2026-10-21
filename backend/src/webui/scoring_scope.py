"""Resolve scored cases from saved scoring results, never from answer existence."""
import json


def _result_ids(data, field, expected=None):
    rows = data.get(field)
    if not isinstance(rows, list):
        return None
    ids = {row.get('instance_id') for row in rows if isinstance(row, dict)}
    if any(not isinstance(value, str) or not value for value in ids):
        return None
    if expected is not None and len(ids) != expected:
        return None
    return ids


def scored_case_ids(folder):
    try:
        return _read_scored_case_ids(folder)
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _read_scored_case_ids(folder):
    martian = folder / 'metrics/martian.json'
    if martian.is_file():
        data = json.loads(martian.read_text(encoding='utf-8-sig'))
        return _result_ids(data, 'results') if data.get('status') == 'complete' else None
    paths = sorted((folder / 'metrics').glob('*_round_*.json'))
    average = folder / 'metrics/metrics_ocr_average.json'
    if average.is_file():
        data = json.loads(average.read_text(encoding='utf-8-sig'))
        if data.get('eval_rounds') != len(paths):
            return None
    else:
        paths = sorted((folder / 'metrics').glob('*.json'))
        if len(paths) != 1:
            return None
    scopes = [_round_ids(path) for path in paths]
    if not scopes or any(scope is None for scope in scopes):
        return None
    if any(scope != scopes[0] for scope in scopes):
        return None
    return scopes[0]


def _round_ids(path):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    count = data.get('summary', {}).get('evaluated_instances')
    if type(count) is not int or count < 0:
        return None
    return _result_ids(data, 'eval_res', count)
