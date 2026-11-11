"""Verify full native DSH loading records against the actual model-channel input.

A batch may bind one Skill member (N=1, single-valued cfg or a member-list
cfg with one member) or a Skill 组合 of several members.  Every bound member
gets its own expected injection block built from ``<skill_content
name="candidate-skill-i">`` and is matched name-by-name across both evidence
channels (explicit ``user/message`` skill-invocation or ``tool/call`` skill
load).  A member without a proof fails the whole loading gate with the
non-retryable ''缺少完整 Skill 实际加载证据'' verdict.

Result shapes (new vs legacy, see _answer_skill / _load_record):

* ``answer['skill']`` and ``.skill-load.json`` stay observably single-valued
  for one-member batches: ``{mode, version, name, loaded, evidence}`` (and
  ``{status: 'missing', version}`` when the proof is absent), exactly like the
  pre-combination code, so historical batch files keep their shape.
* N>=2 combinations become ``{mode: 'skill', members: [{version, name,
  loaded, evidence}]}`` in the answer and a bare member list
  ``[{mode, version, name, loaded, evidence}]`` in ``.skill-load.json``; the
  per-member ``loaded`` flags make the failing member(s) explicit.
"""
import hashlib
from html import escape
import json
from posixpath import join as posixjoin
from pathlib import Path, PurePosixPath

from runner.storage import save
from . import selection
from .jsonextract import loads_tolerant
from .supervisor import evidence_path


def _text(blocks):
    return '\n'.join(b.get('text', '') for b in blocks if b.get('type') == 'text')


def _content_roots(cfg, evidence):
    """Per-member content directory roots in frozen member order.

    Returns ``(new_layout, roots)``.  Sandbox roots are the container paths
    runtime.command mounts (``/skills/<content_sha256>`` per member, legacy
    ``/skill``); POD roots are the host paths under the manifest's
    ``execution_root`` that scope.mjs/candidate.mjs register (``<root>/skills/
    <content_sha256>/content`` per member, legacy ``<root>/skill-version/
    content``).  The same roots feed the expected-block base directory and the
    archived-call attribution.
    """
    new = selection.uses_new_layout(cfg)
    pairs = selection.members(cfg)
    if cfg['dsh_policy'].get('runtime_mode') != 'pod':
        roots = ['/skills/' + record['content_sha256'] if new else '/skill'
                 for _, record in pairs]
        return new, roots
    manifest = json.loads(evidence_path(evidence, '.manifest.json').read_text(encoding='utf-8'))
    if new:
        roots = [posixjoin(manifest['execution_root'], 'skills', record['content_sha256'], 'content')
                 for _, record in pairs]
    else:
        roots = [posixjoin(manifest['execution_root'], 'skill-version', 'content')
                 for _ in pairs]
    return new, roots


def _member_name(new, index):
    return f'candidate-skill-{index + 1}' if new else 'candidate-skill'


def _member_block(name, run, new, record, root, redact):
    source = run / 'skills' / record['content_sha256'] / 'content' if new else run / 'skill' / 'content'
    main = (source / record['main']).read_bytes().decode('utf-8')
    path = posixjoin(root, record['main'])
    base = escape(str(PurePosixPath(path).parent), quote=False)
    return redact('\n'.join(['<skill_content name="' + name + '">', '<skill_resources>',
        'Base directory for this skill: ' + base,
        'Resolve relative paths mentioned by this skill against the base directory before using them. Load referenced resources only as needed.',
        '</skill_resources>', '', '<skill_instructions>', main, '</skill_instructions>', '</skill_content>']))


def _expected(cfg, run, redact, evidence):
    """One expected injected block per frozen member, in frozen member order.

    The returned tuples are ``(name, version, record, expected_text)``; each
    text mirrors the pinned runtime's renderSkillContent verbatim (T02).
    """
    new, roots = _content_roots(cfg, evidence)
    return [(_member_name(new, index), version, record,
             _member_block(_member_name(new, index), run, new, record, roots[index], redact))
            for index, (version, record) in enumerate(selection.members(cfg))]


def _injection(row, name, expected):
    data = row.get('data', {})
    source = data.get('source', {})
    return (row.get('type') == 'user/message' and source.get('kind') == 'skill-invocation'
            and source.get('name') == name and source.get('form') == 'instructions'
            and _text(data.get('content', [])) == expected)


def _tool_load(row, rows, name, expected):
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
    if json.loads(calls[0].get('arguments', '{}')) != {'name': name}:
        return None
    return call_id


def _sessions(evidence, session_id):
    sessions = []
    for path in sorted(evidence.parent.glob(evidence.name + '.session-*.jsonl')):
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
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


def _proof(rows, inputs, name, expected):
    proof = []
    for row in rows:
        injected = _injection(row, name, expected)
        call_id = None if injected else _tool_load(row, rows, name, expected)
        if not injected and not call_id:
            continue
        index = _request_index(inputs, 'user' if injected else 'tool', expected, call_id)
        if index is not None:
            proof.append(dict(form='explicit_injection' if injected else 'tool_load',
                              session_seq=row['seq'], request=index,
                              text_sha256=hashlib.sha256(expected.encode()).hexdigest()))
    return proof


def _load_record(members, complete):
    """.skill-load.json content.

    Reading rule (old files keep their shape): a top-level dict is the
    historical single-valued record (``{status: 'missing', version}`` or
    ``{mode, version, name, loaded, evidence}``); a top-level list is the
    N>=2 combination member list ``[{mode, version, name, loaded, evidence}]``
    whose ``loaded`` flags show which member(s) failed on the missing path.
    """
    if len(members) == 1:
        member = members[0]
        if not complete:
            return dict(status='missing', version=member['version'])
        return dict(mode='skill', version=member['version'], name=member['name'],
                    loaded=True, evidence=member['evidence'])
    return [dict(member) for member in members]


def _answer_skill(members):
    """``answer['skill']`` value.

    One-member batches stay observably the historical single-valued object
    (N=1 new-format batches and legacy batches are interchangeable for every
    consumer); N>=2 combinations become ``{mode: 'skill', members: [{version,
    name, loaded, evidence}]}``.  Readers discriminate on ``mode`` first and
    then on the presence of a ``members`` list.
    """
    if len(members) == 1:
        member = members[0]
        return dict(mode='skill', version=member['version'], name=member['name'],
                    loaded=True, evidence=member['evidence'])
    return dict(mode='skill', members=[{key: member[key] for key in ('version', 'name', 'loaded', 'evidence')}
                                       for member in members])


def _complete_review(events):
    """A final event carrying a parsed `complete` verdict counts as a delivered
    review: the Skill met its contract inside the allowed surface."""
    for event in events:
        if event.get('type') != 'final':
            continue
        parsed = loads_tolerant(event.get('text', ''))
        if isinstance(parsed, dict) and parsed.get('status') == 'complete':
            return True
    return False


def _require_supported(evidence, rows, events, allowed):
    # Skill mode may legitimately use write/edit/bash next to the inspection
    # tools; only capabilities outside the frozen policy count as evidence that
    # the Skill depends on a disabled one.  Attribution to a member context is
    # implicit in the recorded tool name/arguments (member names and content
    # roots appear there), so no extra member mapping is needed for audit.
    calls_outside = [dict(name=r['data']['name'], arguments=r['data'].get('arguments'), session_seq=r['seq'])
                     for r in rows if r.get('type') == 'tool/call'
                     and r['data'].get('name') not in allowed]
    denials = _policy_denials(rows)
    basis = []
    for event in events:
        if event.get('type') != 'final':
            continue
        answer = loads_tolerant(event.get('text', ''))
        if isinstance(answer, dict) and answer.get('status') == 'unsupported':
            basis.append(dict(name='declared_requirement', reason=answer.get('reason')))
    # A stray out-of-policy tool call (e.g. a hallucinated ``json`` probe) or a
    # path-level scope denial is evidence of a capability the Skill *depends*
    # on only when the Skill could not complete inside the allowed surface.  A
    # complete review shows the documented workflow was satisfied; the calls
    # are then recorded for audit without vetoing the answer.
    if not _complete_review(events):
        basis.extend(calls_outside)
        basis.extend(denials)
    status = dict(status='unsupported' if basis else 'supported',
                  basis=basis, denied_exploration=([] if basis else calls_outside + denials))
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


def _allowed(cfg):
    return set(cfg.get('dsh_policy', {}).get('tools') or ['read', 'glob', 'grep'])


def _call_data(line):
    if not line.strip():
        return None
    try:
        row = json.loads(line)
    except ValueError:
        return None
    return row.get('data') if row.get('type') == 'tool/call' else None


def _skill_call_name(arguments, names):
    try:
        parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
    except ValueError:
        return None
    if isinstance(parsed, dict) and parsed.get('name') in names:
        return parsed['name']
    return None


def member_call_counts(cfg, run, item):
    """Per-member ''actually invoked'' counters from the archived call events.

    Audit-level reporting detail, never a gate: a loading proof proves
    ''loaded'', the archived calls show ''used''.  A call is the skill-loading
    tool itself (arguments ``{"name": <member>}``) or any tool call whose JSON
    arguments mention the member's content root.  Counting a call never proves
    the skill text was read and never affects the verdict.
    """
    if cfg['skill_mode'] != 'skill':
        return {}
    evidence = run / 'raw' / item.safe_id
    new, roots = _content_roots(cfg, evidence)
    names = [_member_name(new, index) for index in range(len(roots))]
    counts = dict.fromkeys(names, 0)
    for path in sorted(evidence.parent.glob(evidence.name + '.session-*.jsonl')):
        for line in path.read_text(encoding='utf-8').splitlines():
            data = _call_data(line)
            if data is None:
                continue
            if data.get('name') == 'skill':
                member = _skill_call_name(data.get('arguments'), names)
                if member is not None:
                    counts[member] += 1
                continue
            blob = json.dumps(data.get('arguments', {}), ensure_ascii=False)
            for name, root in zip(names, roots):
                if root in blob:
                    counts[name] += 1
    return counts


def verify_loading(cfg, run, evidence, events, redact):
    """Prove every bound Skill member was actually loaded.

    Returns (result, sessions).  Raises the non-retryable ''缺少完整 Skill
    实际加载证据'' verdict when any member lacks a load proof, before any
    answer-format judgement is made.
    """
    if cfg['skill_mode'] == 'baseline':
        return dict(mode='baseline', loaded=False), []
    inputs = json.loads(evidence_path(evidence, '.model-inputs.json').read_text(encoding='utf-8'))
    ids = [e.get('sessionId') for e in events if e.get('type') == 'session']
    if len(ids) != 1:
        raise ValueError('Skill 会话身份不完整')
    sessions = _sessions(evidence, ids[0])
    members = []
    for name, version, record, expected in _expected(cfg, run, redact, evidence):
        proof = _proof(sessions, inputs, name, expected)
        members.append(dict(mode='skill', version=version, name=name,
                            loaded=bool(proof), evidence=proof))
    if not all(member['loaded'] for member in members):
        save(evidence_path(evidence, '.skill-load.json'), _load_record(members, complete=False))
        raise ValueError('缺少完整 Skill 实际加载证据，禁止评分')
    result = _answer_skill(members)
    save(evidence_path(evidence, '.skill-load.json'), _load_record(members, complete=True))
    return result, sessions


def require_supported(cfg, evidence, sessions, events):
    """Record and veto capabilities outside the frozen policy surface."""
    _require_supported(evidence, sessions, events, _allowed(cfg))


def verify(cfg, run, evidence, events, redact):
    result, sessions = verify_loading(cfg, run, evidence, events, redact)
    if cfg['skill_mode'] == 'skill':
        require_supported(cfg, evidence, sessions, events)
    return result
