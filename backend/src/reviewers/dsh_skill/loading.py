"""Verify full native DSH loading records against the actual model-channel input."""
import hashlib
from html import escape
import json
from pathlib import Path, PurePosixPath

from runner.storage import save
from .supervisor import evidence_path


def _text(blocks):
    return '\n'.join(b.get('text', '') for b in blocks if b.get('type') == 'text')


def _expected(cfg, run, redact, evidence):
    record = cfg['skill_identity']
    path = '/skill/' + record['main']
    if cfg['dsh_policy'].get('runtime_mode') == 'pod':
        manifest = json.loads(evidence_path(evidence, '.manifest.json').read_text())
        path = str(Path(manifest['execution_root']) / 'skill-version/content' / record['main'])
    main = (run / 'skill/content' / record['main']).read_bytes().decode('utf-8')
    base = escape(str(PurePosixPath(path).parent), quote=False)
    return redact('\n'.join(['<skill_content name="candidate-skill">', '<skill_resources>',
        'Base directory for this skill: ' + base,
        'Resolve relative paths mentioned by this skill against the base directory before using them. Load referenced resources only as needed.',
        '</skill_resources>', '', '<skill_instructions>', main, '</skill_instructions>', '</skill_content>']))


def _injection(row, expected):
    data = row.get('data', {})
    source = data.get('source', {})
    return (row.get('type') == 'user/message' and source.get('kind') == 'skill-invocation'
            and source.get('name') == 'candidate-skill' and source.get('form') == 'instructions'
            and _text(data.get('content', [])) == expected)


def _tool_load(row, rows, expected):
    if row.get('type') != 'tool/result':
        return None
    message = row.get('data', {}).get('message', {})
    if message.get('isError') or _text(message.get('content', [])) != expected:
        return None
    call_id = message.get('toolCallId')
    calls = [r['data'] for r in rows if r.get('type') == 'tool/call'
             and r['seq'] < row['seq'] and r['data'].get('callId') == call_id]
    if len(calls) != 1 or calls[0].get('name') != 'skill':
        return None
    if json.loads(calls[0].get('arguments', '{}')) != {'name': 'candidate-skill'}:
        return None
    return call_id


def _sessions(evidence, session_id):
    sessions = []
    for path in sorted(evidence.parent.glob(evidence.name + '.session-*.jsonl')):
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if rows and rows[0].get('type') == 'session' and rows[0].get('id') == session_id:
            sessions.extend(rows)
    return sessions


def _request_index(inputs, role, expected, call_id):
    for index, request in enumerate(inputs):
        for message in request.get('messages', []):
            if (message.get('role') == role and message.get('content') == expected
                    and (call_id is None or message.get('tool_call_id') == call_id)):
                return index + 1
    return None


def _proof(rows, inputs, expected):
    proof = []
    for row in rows:
        injected = _injection(row, expected)
        call_id = None if injected else _tool_load(row, rows, expected)
        if not injected and not call_id:
            continue
        index = _request_index(inputs, 'user' if injected else 'tool', expected, call_id)
        if index is not None:
            proof.append(dict(form='explicit_injection' if injected else 'tool_load',
                              session_seq=row['seq'], request=index,
                              text_sha256=hashlib.sha256(expected.encode()).hexdigest()))
    return proof


def _require_supported(evidence, rows, events):
    basis = [dict(name=r['data']['name'], arguments=r['data'].get('arguments'), session_seq=r['seq'])
             for r in rows if r.get('type') == 'tool/call'
             and r['data'].get('name') not in {'read', 'glob', 'grep', 'skill'}]
    basis.extend(_policy_denials(rows))
    for event in events:
        if event.get('type') != 'final':
            continue
        try:
            answer = json.loads(event.get('text', ''))
        except ValueError:
            continue
        if isinstance(answer, dict) and answer.get('status') == 'unsupported':
            basis.append(dict(name='declared_requirement', reason=answer.get('reason')))
    status = dict(status='unsupported' if basis else 'supported', basis=basis)
    save(evidence_path(evidence, '.skill-support.json'), status)
    if basis:
        raise ValueError('Skill 依赖禁用能力，不支持本期只读检视；不发布质量分')


def _policy_denials(rows):
    calls, basis = {}, []
    for row in rows:
        if row.get('type') == 'tool/call':
            calls[row['data']['callId']] = row
        if row.get('type') != 'tool/result':
            continue
        message = row.get('data', {}).get('message', {})
        text = _text(message.get('content', []))
        marker = text.removeprefix('Error: ').removeprefix('Error: ')
        if not message.get('isError') or not marker.startswith('BENCH_DSH_SCOPE_DENIED:'):
            continue
        call = calls.get(message.get('toolCallId'))
        if call is not None:
            basis.append(dict(name=call['data']['name'], arguments=call['data'].get('arguments'),
                              session_seq=call['seq'], result_session_seq=row['seq'], denial=text))
    return basis


def verify(cfg, run, evidence, events, redact):
    if cfg['skill_mode'] == 'baseline':
        return dict(mode='baseline', loaded=False)
    expected = _expected(cfg, run, redact, evidence)
    inputs = json.loads(evidence_path(evidence, '.model-inputs.json').read_text())
    ids = [e.get('sessionId') for e in events if e.get('type') == 'session']
    if len(ids) != 1:
        raise ValueError('Skill 会话身份不完整')
    sessions = _sessions(evidence, ids[0])
    proof = _proof(sessions, inputs, expected)
    if not proof:
        save(evidence_path(evidence, '.skill-load.json'), dict(status='missing', version=cfg['skill_version']))
        raise ValueError('缺少完整 Skill 实际加载证据，禁止评分')
    result = dict(mode='skill', version=cfg['skill_version'], name=cfg['skill_identity']['name'],
                  loaded=True, evidence=proof)
    save(evidence_path(evidence, '.skill-load.json'), result)
    _require_supported(evidence, sessions, events)
    return result
