"""Replay the fixed skill-support verdict against archived session rows using
the project's OWN loading logic (no reimplemented approximation).

Monkeypatches runner.storage.save to a no-op so the archived evidence files in
<raw-dir> are never overwritten, then feeds each case's redacted session rows
and stdout events through loading._require_supported exactly as verify() does.
The classification printed is what a re-run with these fixes would decide.

Usage:
    python tools/replay_support_verdict.py <archive-raw-dir>
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/src'))

from reviewers.dsh_skill import loading  # noqa: E402
from runner import storage  # noqa: E402

storage.save = lambda *args, **kwargs: None  # never touch archived evidence


def sessions_rows(case_dir, name):
    rows = []
    for path in sorted(case_dir.glob(name + '.session-*.jsonl')):
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
    allowed = {'read', 'glob', 'grep', 'write', 'edit', 'bash', 'skill'}
    counts = dict(supported=0, unsupported=0, no_final=0)
    details = []
    for support in sorted(root.glob('*.skill-support.json')):
        # Defend against debris left in the archive by an earlier interrupted
        # run of this replay (<case>.evidence.skill-support.json).
        if '.evidence' in support.name:
            continue
        case = support.name.removesuffix('.skill-support.json')
        # Evidence is written through monkeypatched storage.save (a no-op), so
        # a scratch path outside the archive keeps the run-tree unmodified.
        evidence = Path(tempfile.mkdtemp(prefix='dsh-support-replay-')) / 'case'
        evidence.mkdir(parents=True, exist_ok=True)
        sessions = sessions_rows(root, case)
        events = []
        stdout_path = root / (case + '.stdout.jsonl')
        if stdout_path.exists():
            for line in stdout_path.read_text(encoding='utf-8').splitlines():
                if line.strip():
                    events.append(json.loads(line))
        try:
            loading._require_supported(evidence, sessions, events, allowed)
            verdict = 'SUPPORTED'
            counts['supported'] += 1
        except ValueError as error:
            basis = None
            support_path = root / (case + '.skill-support.json')
            if support_path.exists():
                record = json.loads(support_path.read_text(encoding='utf-8'))
                basis = ','.join(item.get('name', '?') for item in record.get('basis', []))
            if '最终答卷' in str(error) or '加载' in str(error):
                verdict = 'no-final [' + str(error)[:40] + ']'
                counts['no_final'] += 1
            else:
                verdict = 'unsupported [' + (basis or '?') + ']'
                counts['unsupported'] += 1
        details.append(f"{case:48s} {verdict}")
    for line in details:
        print(line)
    print('\n=== fixed verdict distribution ===')
    for key, value in counts.items():
        print(f"  {key}: {value}")


if __name__ == '__main__':
    main()
