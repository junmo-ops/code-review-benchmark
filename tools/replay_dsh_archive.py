"""Replay an archived DSH batch through the current (fixed) adapter decision logic.

Does not re-execute DSH.  For every executed case it reconstructs what the
current pipeline would decide today from the archived evidence, so a fix can be
verified against the exact traces that failed.  Writes nothing into the
archive; evidence is copied to a scratch run tree.

Usage:
    python tools/replay_dsh_archive.py --results-dir <archive-root> [--keep]
"""
import argparse
import json
from pathlib import Path
import shutil
import socketserver
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/src'))

if not hasattr(socketserver, 'ThreadingUnixStreamServer'):
    socketserver.ThreadingUnixStreamServer = type('ThreadingUnixStreamServer', (), {})

from reviewers.dsh_skill import adapter, answer, loading  # noqa: E402

SUFFIXES = ('.execution.json', '.manifest.json', '.requests.json', '.model-inputs.json',
            '.stdout.jsonl', '.skill-support.json', '.skill-load.json')


def _rows(evidence, suffix):
    path = evidence.with_name(evidence.name + suffix)
    if not path.exists():
        return []
    text = path.read_text(encoding='utf-8')
    if suffix == '.stdout.jsonl':
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def _item(dataset, instance_id):
    row = next((r for r in dataset if r['instance_id'] == instance_id), None)
    if row is None:
        return None
    return type('Item', (), dict(instance_id=row['instance_id'], repo=row['repo'],
                                 base_commit=row['base_commit'], head_commit=row['head_commit']))


def _decision(cfg, run, item, safe_id):
    """What the fixed pipeline decides for one archived case, or a reason string."""
    evidence = run / 'raw' / safe_id
    raw = _rows(evidence, '.stdout.jsonl')
    execution = _rows(evidence, '.execution.json') or {}
    if execution.get('exit_code', 1) != 0:
        return ('execution-failed', adapter._execution_error('\n'.join(
            json.dumps(row, ensure_ascii=False) for row in raw)))
    try:
        skill = loading.verify(cfg, run, evidence, raw, lambda text: text)
        manifest = _rows(evidence, '.manifest.json')
        requests = _rows(evidence, '.requests.json')
        result = answer.convert(item, cfg, raw, manifest.get('lines', {}), requests)
        return ('complete', f"{len(result['review']['comments'])} 条意见；skill={skill.get('status')}")
    except ValueError as error:
        return ('rejected', str(error))


def _copy_case(run, source, safe_id):
    evidence = run / 'raw' / safe_id
    evidence.mkdir(parents=True)
    for suffix in SUFFIXES:
        path = source.with_name(source.name + suffix)
        if path.is_file():
            shutil.copyfile(path, evidence.with_name(evidence.name + suffix))
    # Session files are evidence siblings (<safe>.session-<hash>.jsonl), not
    # children of the evidence base; keep that layout for loading._sessions.
    for session in sorted(source.parent.glob(source.name + '.session-*.jsonl')):
        shutil.copyfile(session, evidence.with_name(session.name))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results-dir', required=True, help='archived results dir')
    parser.add_argument('--keep', action='store_true', help='keep the scratch tree')
    args = parser.parse_args()
    archive = Path(args.results_dir)
    frozen = json.loads((archive / 'dsh-frozen-config.json').read_text(encoding='utf-8'))
    dataset = [json.loads(line) for line in
               (archive / 'dataset.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    scratch = Path(tempfile.mkdtemp(prefix='dsh-replay-'))
    if args.keep:
        print(f'scratch: {scratch}')
    shutil.copytree(archive / 'skill', scratch / 'skill')
    outcomes = sorted((archive / 'raw').glob('*.outcome.json'))
    print(f"replay {len(outcomes)} executed cases; skill_mode={frozen.get('skill_mode')} "
          f"tools={frozen.get('dsh_policy', {}).get('tools')}")
    for outcome in outcomes:
        safe_id = outcome.name.removesuffix('.outcome.json')
        item = _item(dataset, safe_id)
        if item is None:
            continue
        _copy_case(scratch, archive / 'raw' / safe_id, safe_id)
        old = json.loads(outcome.read_text(encoding='utf-8'))
        old_status, old_reason = old.get('status'), old.get('reason')
        kind, detail = _decision(frozen, scratch, item, safe_id)
        print(f'- {safe_id}\n    archived: [{old_status}] {old_reason}'
              f"\n    today:    [{kind}] {detail}")
    if not args.keep:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == '__main__':
    main()
