"""On-demand experiment reads without changing frozen execution modules."""
import json


def experiment(app, key, *, compact=False, jobs=None):
    folder = app.run_dir(key)
    path = folder / 'experiment.json'
    if path.is_symlink() or folder.is_symlink():
        raise ValueError('实验路径不可用')
    m = json.loads(path.read_text(encoding='utf-8'))
    jobs = app.jobs.values() if jobs is None else jobs
    job = max((j for j in jobs if j.get('key') == key),
              key=lambda j: j.get('created_at', ''), default=None)
    value = dict(key=key, name=m['name'], suite=m['suite'], judge_model=m['judge_model'],
                 created_at=m['created_at'], job_id=job['id'] if job else None,
                 status=job['status'] if job else 'interrupted',
                 case_count=len(m['case_ids']), group_count=len(m['groups']),
                 budget=dict(review_seconds=m.get('case_timeout_seconds'),
                             judge_seconds=m.get('judge_case_timeout_seconds'), protocol=m.get('protocol')))
    if not compact:
        value['report'] = json.loads((folder / 'comparison.json').read_text(encoding='utf-8'))
    return value


def list_experiments(app, *, compact=False):
    values = []
    with app.lock:
        jobs = [dict(job) for job in app.jobs.values()]
    for path in (app.data / 'runs').glob('compare-*/experiment.json'):
        try:
            values.append(experiment(app, path.parent.name, compact=compact, jobs=jobs))
        except (OSError, ValueError, KeyError):
            continue
    return sorted(values, key=lambda value: value['created_at'], reverse=True)
