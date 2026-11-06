"""Mechanism-level verification of the DSH harness fixes against the real binary.

Runs the REAL DSH 0.2.1-rc.2 headless binary from a local offline install
against loopback mock model endpoints, proving at the mechanism level that:

  1. compaction: with the archived config (threshold ~33.9k tokens, retries=0)
     a growing session aborts the turn fail-closed (exit != 0, turn_end
     aborted); with the fixed config (headroomTokens=16384 -> threshold ~83.0k,
     compactionRetries=1) the same session continues and completes (exit 0).
  2. headers timeout: the undici dispatcher bridge.mjs installs raises the
     global-fetch headers/body timeout to the SDK allowance; a 15 s first-byte
     delay succeeds at the default bound and fails fast at a 5 s test bound.
  3. write surface: with skill-mode write enabled, tool-fs writes inside the
     case succeed while reads outside the case are denied (BENCH_DSH_SCOPE_DENIED).

Usage:
    python tools/dsh_repro_mechanism.py [--offline-root D:\\devSoftware\\dsh]
Requires Node >= 22.19 on PATH and the offline DSH install.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/src'))

import socketserver  # noqa: E402

if not hasattr(socketserver, 'ThreadingUnixStreamServer'):
    socketserver.ThreadingUnixStreamServer = type('ThreadingUnixStreamServer', (), {})

from reviewers.dsh_skill import policy, runtime  # noqa: E402

PASS, FAIL = 0, 1


class MockHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({'object': 'list', 'data': [{'id': 'bench-model', 'object': 'model'}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        server = self.server.mock
        if server.delay_ms:
            time.sleep(server.delay_ms / 1000.0)
        size = int(self.headers.get('Content-Length', '0'))
        payload = json.loads(self.rfile.read(size))
        server.records.append(payload)
        message, reason, usage = server.respond(payload, len(server.records))
        chunk = dict(id='mech', object='chat.completion.chunk', created=1,
                     model=payload.get('model', 'bench-model'),
                     choices=[dict(index=0, delta=dict(role='assistant', **message), finish_reason=reason)],
                     usage=usage)
        body = 'data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\ndata: [DONE]\n\n'
        data = body.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # client aborted (e.g. an intentional headers-timeout probe)

    def log_message(self, *args):
        # BaseHTTPRequestHandler logs every request to stderr by default;
        # the mock's traffic is noise, so keep it off the scenario output.
        pass


class MockModel:
    """Loopback OpenAI-completions endpoint with a scripted responder."""

    def __init__(self, respond, delay_ms=0):
        self.records = []
        self.respond = respond
        self.delay_ms = delay_ms
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), MockHandler)
        self.httpd.mock = self
        self.port = self.httpd.server_port
        self.url = f'http://127.0.0.1:{self.port}/v1'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


def usage(seen_chars, emitted_chars):
    return dict(prompt_tokens=max(1, seen_chars // 4), completion_tokens=max(1, emitted_chars // 4),
                total_tokens=max(1, (seen_chars + emitted_chars) // 4),
                prompt_tokens_details=dict(cached_tokens=0))


def read_tool(path):
    return dict(id='read-' + str(hash(path) & 0xffff), type='function',
                function=dict(name='read', arguments=json.dumps(dict(file_path=str(path)))))


def write_tool(path, content):
    return dict(id='write-' + str(hash(path) & 0xffff), type='function',
                function=dict(name='write', arguments=json.dumps(dict(file_path=str(path), content=content))))


SUMMARY_CHECKPOINT = ('## Primary Request and Intent\n- verify compaction behaviour under the harness fix\n\n'
                      '## Key Technical Concepts\n(none)\n\n## Files and Code\n(none)\n\n'
                      '## Errors and Fixes\n(none)\n\n## Pending Jobs\n(none)\n\n'
                      '## Current Work\n- mechanism repro is running\n\n## Next Step\n(none)\n\n'
                      '## Critical Context\n- compaction must stay fail-closed on real overflow')


def grower(work, filler_chars, giant_chars):
    """Session-growth responder mirroring the archived pressure profile.

    Reads, then moderate assistant blocks, then two giant tool-free assistant
    messages (standing in for a long reasoning tail since the last event).
    That tail exceeds the archived 33.9k threshold but not the fixed one, so
    with retries=0 a single compaction attempt cannot fall below threshold and
    the turn aborts fail-closed, while compactionRetries=1 prunes the tail on
    a second attempt and the turn completes.

    Requests whose last user message opens dsh-compaction-basic's summarization
    directive are compaction calls and get a tiny checkpoint instead. Usage is
    reported from the actual request body so llm-pi-ai's context guard sees
    honest numbers (an accumulating counter would overflow it).
    """
    state = {'count': 0, 'big': work / 'repo' / 'big.txt'}

    def respond(payload, index):
        state['count'] = index
        prompt_tokens = max(1, len(json.dumps(payload, ensure_ascii=False)) // 4)
        tail = payload['messages'][-1] if payload['messages'] else {}
        if tail.get('role') == 'user' and isinstance(tail.get('content', ''), str) \
                and tail['content'].startswith('You are now acting as a compaction engine'):
            return dict(content=SUMMARY_CHECKPOINT), 'stop', usage(prompt_tokens, 0)
        if index <= 2:
            return dict(tool_calls=[read_tool(state['big'])]), 'tool_calls', usage(prompt_tokens, 0)
        if 8 <= index <= 9:
            filler = 'A' * giant_chars
            return dict(content=filler, tool_calls=[read_tool(state['big'])]), 'tool_calls', \
                usage(prompt_tokens, (len(filler) + 3) // 4)
        if index >= 12:
            text = json.dumps({'status': 'complete', 'comments': []})
            return dict(content=text), 'stop', usage(prompt_tokens, (len(text) + 3) // 4)
        filler = 'A' * filler_chars
        return dict(content=filler, tool_calls=[read_tool(state['big'])]), 'tool_calls', \
            usage(prompt_tokens, (len(filler) + 3) // 4)

    return respond


def build_work():
    work = Path(tempfile.mkdtemp(prefix='dsh-mech-'))
    (work / 'repo').mkdir()
    (work / 'input').mkdir()
    (work / 'home').mkdir()
    (work / 'repo' / 'big.txt').write_text('B' * 200_000)
    (work / 'input' / 'REVIEW_DIFF.patch').write_text('')
    (work / 'input' / 'REVIEW_REQUEST.json').write_text('{}')
    return work


def _find_row(rows, row_id):
    """Walk both direct id rows and insert-wrapped plugin rows."""
    for row in rows:
        if row.get('id') == row_id:
            return row
        for inserted in row.get('insert', []):
            if inserted.get('id') == row_id:
                return inserted
    return None


def launch_policy(offline_modules, work, port, skill=False, writes=None, old_compaction=False, patch_extra=None):
    import unittest.mock as mock
    with mock.patch.object(runtime, 'MODULES', offline_modules), \
            mock.patch.dict(os.environ, BENCH_DSH_RUNTIME_MODE='pod'):
        if skill is False:
            skill_config = None
        elif skill is True:
            skill_config = dict(id='mech-skill', name='candidate-skill', description='mechanism',
                                main='main.md')
        else:
            skill_config = dict(skill)
        rows = json.loads(policy.overlay('bench-model', skill_config, work=work))
    _find_row(rows, 'llm-pi-ai')['config']['providers']['benchmark']['baseURL'] = f'http://127.0.0.1:{port}/v1'
    if old_compaction:
        _find_row(rows, 'benchmark-compaction')['config'] = \
            dict(compactionRetries=0, maxOverflowRetries=0, headroomTokens=65536)
    if writes is not None:
        _find_row(rows, 'tool-fs')['config'].update(write=writes, edit=writes)
        _find_row(rows, 'sandbox-policy')['config']['mode'] = 'workspace-write' if writes else 'read-only'
        _find_row(rows, 'benchmark-tool-scope')['config']['writes'] = writes
    path = work / 'launch-policy.json'
    path.write_text(json.dumps(rows), encoding='utf-8')
    return path


def run_dsh(offline, work, policy_path, use_bridge=False, timeout=420):
    entry = offline / 'node_modules/@deepseek-ai/dsh/lib/bin.js'
    command = [str(offline / 'node.exe') if (offline / 'node.exe').is_file() else 'node']
    if use_bridge:
        command += ['--import', Path(ROOT / 'scripts/dsh/bridge.mjs').as_uri()]
    command += [str(entry), '--profile', 'headless', '--patch', str(policy_path),
                '--json', 'Review the head snapshot in /work against /input/REVIEW_DIFF.patch. '
                           'Reply with exactly one JSON object.']
    env = dict(os.environ, HOME=str(work / 'home'), DSH_HOME=str(work / 'home'),
               DSH_TELEMETRY_DISABLED='1', BENCH_DSH_KEY='mechanism-secret',
               BENCH_DSH_RUNTIME=str(offline))
    return subprocess.run(command, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', env=env,
                          timeout=timeout, cwd=str(work / 'repo'))


def run_compaction(offline, old_config, filler_chars, giant_chars):
    work = build_work()
    (work / 'repo' / 'big.txt').write_text('B' * 40_000)
    mock = MockModel(grower(work, filler_chars, giant_chars))
    try:
        policy_path = launch_policy(offline / 'node_modules', work, mock.port, old_compaction=old_config)
        record = run_dsh(offline, work, policy_path)
        return record, mock
    finally:
        mock.close()
        shutil.rmtree(work, ignore_errors=True)


def _aborted_turn(stdout):
    return any('"turn_end"' in line and '"aborted"' in line for line in stdout.splitlines())


def scenario_compaction(offline):
    """Old config aborts the turn; fixed config completes the same session."""
    candidates = [(24_000, 260_000), (36_000, 320_000), (48_000, 400_000)]
    findings = []
    last = None
    for filler, giant in candidates:
        old_record, _ = run_compaction(offline, True, filler, giant)
        last = old_record
        if old_record.returncode != 0 and _aborted_turn(old_record.stdout):
            findings.append((filler, giant, old_record.returncode, 'aborted'))
            break
        findings.append((filler, giant, old_record.returncode,
                         'aborted' if _aborted_turn(old_record.stdout) else 'completed-unexpectedly'))
    if not findings or findings[-1][3] != 'aborted':
        print(f'  FAIL compaction-old: session never fail-closed aborted with old config '
              f'(stdout tail: {last.stdout[-400:]} stderr: {last.stderr[-400:]})')
        return FAIL
    filler, giant, _, _ = findings[-1]
    new_record, _ = run_compaction(offline, False, filler, giant)
    if new_record.returncode != 0 or _aborted_turn(new_record.stdout):
        print(f'  FAIL compaction-new: fixed config aborted too '
              f'(stdout tail: {new_record.stdout[-500:]} stderr: {new_record.stderr[-600:]})')
        return FAIL
    print(f'  PASS compaction: old config fail-closed aborted (filler={filler} giant={giant}); '
          f'fixed config completed (exit 0)')
    return PASS


def scenario_headers(offline):
    """bridge.mjs dispatcher bounds the global-fetch headers/body timeout."""
    tmp = Path(tempfile.mkdtemp(prefix='dsh-headers-'))
    shutil.copytree(offline / 'node_modules/undici', tmp / 'node_modules' / 'undici')
    shutil.copyfile(ROOT / 'scripts/dsh/bridge.mjs', tmp / 'bridge.mjs')
    delay = MockModel(lambda p, i: (dict(content='hi'), 'stop', usage(10, 10)), delay_ms=15_000)
    try:
        url = f'http://127.0.0.1:{delay.port}/v1/chat/completions'
        probe = ('const t0 = Date.now();'
                 "try { const r = await globalThis.fetch(%s, { method: 'POST',"
                 "headers: {'content-type': 'application/json'}, body: '{}'});"
                 "console.log('OK ' + (Date.now() - t0) + ' ' + r.status); }"
                 "catch (e) { console.log('ERR ' + (Date.now() - t0) + ' ' + (e.cause ? e.cause.message : e.message)); }")
        env = dict(os.environ)
        bridge_uri = Path(tmp / 'bridge.mjs').as_uri()
        with_bridge = subprocess.run(['node', '--import', bridge_uri, '--input-type=module',
                                      '-e', probe % json.dumps(url)], capture_output=True, text=True,
                                     encoding='utf-8', errors='replace',
                                     env=dict(env, BENCH_DSH_HEADERS_TIMEOUT_MS='5000'),
                                     timeout=90, cwd=str(tmp))
        control = subprocess.run(['node', '--input-type=module', '-e', probe % json.dumps(url)],
                                 capture_output=True, text=True, encoding='utf-8', errors='replace',
                                 timeout=90, cwd=str(tmp))
        positive = subprocess.run(['node', '--import', bridge_uri, '--input-type=module',
                                   '-e', probe % json.dumps(url)], capture_output=True, text=True,
                                  encoding='utf-8', errors='replace',
                                  timeout=90, cwd=str(tmp))
        def last_probe_line(record):
            for line in (record.stdout or '').splitlines():
                if line.startswith('OK ') or line.startswith('ERR '):
                    return line
            return (record.stderr or record.stdout or '').strip().splitlines()[-1]
        # Deployment shape: the bridge lives outside the DSH runtime tree
        # (/opt/deployments/scripts/dsh), where a bare import('undici') cannot
        # resolve. The bridge must then load undici from the BENCH_DSH_RUNTIME
        # tree; run it from a node_modules-free directory to prove the lowered
        # bound still applies via that fallback.
        bare_dir = Path(tempfile.mkdtemp(prefix='dsh-headers-bare-'))
        shutil.copyfile(ROOT / 'scripts/dsh/bridge.mjs', bare_dir / 'bridge.mjs')
        bare_uri = Path(bare_dir / 'bridge.mjs').as_uri()
        bare_env = dict(env, BENCH_DSH_HEADERS_TIMEOUT_MS='5000', BENCH_DSH_RUNTIME=str(offline))
        from_runtime = subprocess.run(['node', '--import', bare_uri, '--input-type=module',
                                       '-e', probe % json.dumps(url)], capture_output=True, text=True,
                                      env=bare_env, timeout=90, cwd=str(bare_dir))
        line_with = last_probe_line(with_bridge)
        line_control = last_probe_line(control)
        line_positive = last_probe_line(positive)
        line_runtime = last_probe_line(from_runtime)
        fast_fail = line_with.startswith('ERR') and 'timeout' in line_with.lower()
        slow_ok = line_control.startswith('OK') and not line_positive.startswith('ERR')
        runtime_fast_fail = line_runtime.startswith('ERR') and 'timeout' in line_runtime.lower()
        if not (fast_fail and slow_ok):
            print(f'  FAIL headers: bridge={line_with!r} control={line_control!r} positive={line_positive!r}')
            return FAIL
        if not runtime_fast_fail:
            print(f'  FAIL headers-runtime-tree: undici load via BENCH_DSH_RUNTIME failed '
                  f'(probe={line_runtime!r} stderr={from_runtime.stderr[-400:]})')
            return FAIL
        print(f'  PASS headers: dispatcher honored (5s bound fails fast on 15s delay; '
              f'300s default and 600s bridge bound succeed; runtime-tree undici load works)')
        return PASS
    finally:
        delay.close()
        shutil.rmtree(tmp, ignore_errors=True)


def writer(prompt):
    """Skill-mode write-then-escape-then-final responder."""
    def respond(payload, index):
        if index == 1:
            return dict(tool_calls=[write_tool(prompt['repo'] / 'issues.json', '{"items":[]}')]), \
                'tool_calls', usage(2000, 0)
        if index == 2:
            return dict(tool_calls=[read_tool('C:/Windows/win.ini')]), 'tool_calls', usage(2000, 0)
        text = json.dumps({'status': 'complete', 'comments': []})
        return dict(content=text), 'stop', usage(2000, len(text))
    return respond


def scenario_write_scope(offline):
    work = build_work()
    mock = MockModel(writer(dict(repo=work / 'repo')))
    try:
        policy_path = launch_policy(offline / 'node_modules', work, mock.port, writes=True)
        record = run_dsh(offline, work, policy_path)
        issues = (work / 'repo' / 'issues.json')
        denied = 'BENCH_DSH_SCOPE_DENIED' in record.stdout or 'BENCH_DSH_SCOPE_DENIED' in record.stderr
        if record.returncode != 0 or not issues.exists() or not denied:
            print(f'  FAIL write-scope: exit={record.returncode} issues_exists={issues.exists()} '
                  f'denied={denied} stderr={record.stderr[-400:]}')
            return FAIL
        print('  PASS write-scope: write inside the case persisted; outside read denied '
              '(exit 0, issues.json written, BENCH_DSH_SCOPE_DENIED observed)')
        return PASS
    finally:
        mock.close()
        shutil.rmtree(work, ignore_errors=True)


def scenario_scope(offline):
    """Skill-mode inspection scope: read-side tools may reach the task input and
    the bound Skill resource directory, while writes stay inside the case."""
    work = build_work()
    (work / 'skill-version' / 'content').mkdir(parents=True)
    (work / 'skill-version' / 'content' / 'main.md').write_text('# candidate skill')
    (work / 'input' / 'REVIEW_DIFF.patch').write_text('--- a/x\n+++ b/x\n')

    def responder(prompt):
        def respond(payload, index):
            # Bash must execute in POD+skill (danger-full-access) instead of
            # failing with SandboxUnavailableError (the gofr-class symptom).
            # tool-bash is disabled on the win32 runtime (bundled cordis rows
            # set disabled: process.platform === 'win32'), so on Windows the
            # probe stays at a denied-tool check; on Linux it must run.
            if index == 1 and not os.name == 'nt':
                return dict(tool_calls=[bash_tool('printf ok > mech-bash.txt')]), 'tool_calls', usage(2000, 0)
            if index == (1 if os.name == 'nt' else 2):
                return dict(tool_calls=[glob_tool(str(work / 'input'), '*')]), 'tool_calls', usage(2000, 0)
            if index == (2 if os.name == 'nt' else 3):
                return dict(tool_calls=[glob_tool(
                    str(work / 'skill-version' / 'content'), '**/*')]), 'tool_calls', usage(2000, 0)
            if index == (3 if os.name == 'nt' else 4):
                return dict(tool_calls=[write_tool(
                    str(work / 'repo' / 'note.txt'), 'inside the case')]), 'tool_calls', usage(2000, 0)
            if index == (4 if os.name == 'nt' else 5):
                return dict(tool_calls=[write_tool(
                    str(work / 'input' / 'tampered.txt'), 'outside')]), 'tool_calls', usage(2000, 0)
            text = json.dumps({'status': 'complete', 'comments': []})
            return dict(content=text), 'stop', usage(2000, len(text))
        return respond

    mock = MockModel(responder(work))
    try:
        policy_path = launch_policy(offline / 'node_modules', work, mock.port, skill=True)
        record = run_dsh(offline, work, policy_path)
        stdout, stderr = record.stdout, record.stderr
        text = stdout + stderr
        note = work / 'repo' / 'note.txt'
        tampered = work / 'input' / 'tampered.txt'
        # Read-side globs into input/skill were allowed, the write into the
        # task input was denied, and the case-internal write persisted. Bash is
        # asserted only on Linux, where tool-bash is actually enabled and must
        # not fail with SandboxUnavailableError; on win32 the bundled config
        # disables the tool entirely, so that path is out of scope here.
        bash_ok = os.name == 'nt' or (work / 'mech-bash.txt').exists()
        if record.returncode != 0 or not note.exists() or tampered.exists() \
                or 'BENCH_DSH_SCOPE_DENIED' not in text or not bash_ok \
                or ('SandboxUnavailableError' in text and os.name != 'nt'):
            print(f'  FAIL scope: exit={record.returncode} note={note.exists()} '
                  f'tampered={tampered.exists()} bash_ok={bash_ok} '
                  f'denied={("BENCH_DSH_SCOPE_DENIED" in text)} '
                  f'sandbox_err={("SandboxUnavailableError" in text)} '
                  f'stderr={stderr[-400:]}')
            return FAIL
        print('  PASS scope: pod+skill runs bash without SandboxUnavailableError (Linux), '
              'read-side globs reach input and skill resources, '
              'writes stayed inside the case (exit 0, note.txt written, input write denied)')
        return PASS
    finally:
        mock.close()
        shutil.rmtree(work, ignore_errors=True)


def glob_tool(path, pattern):
    return dict(id='glob-' + str(hash(path) & 0xffff), type='function',
                function=dict(name='glob', arguments=json.dumps(dict(path=str(path), pattern=pattern))))


def bash_tool(command):
    return dict(id='bash-' + str(hash(command) & 0xffff), type='function',
                function=dict(name='bash', arguments=json.dumps(dict(command=command))))


def _make_skill_snapshot(work):
    """A valid frozen Skill version, mirroring the imported store record."""
    from webui.skill_package import content_digest
    root = work / 'skill-version'
    content = root / 'content'
    content.mkdir(parents=True)
    main = 'SKILL.md'
    payload = b'---\nname: mech-skill\ndescription: mechanism\n---\n# candidate\n'
    (content / main).write_bytes(payload)
    files = [dict(path=main, size=len(payload), sha256=hashlib.sha256(payload).hexdigest())]
    digest = content_digest(files)
    record = dict(id=digest, name='mech-skill', description='mechanism', main=main,
                  files=files, content_sha256=digest,
                  archive_sha256=hashlib.sha256(b'').hexdigest())
    (root / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    (root / 'package.zip').write_bytes(b'')
    return record


def bash_writer(prompt):
    """Skill-mode responder: one unfenced bash write into the frozen Skill dir."""
    def respond(payload, index):
        if index == 1 and os.name != 'nt':
            target = json.dumps(str(prompt['skill'] / 'leak.txt'))
            return dict(tool_calls=[bash_tool(f'printf x > {target}')]), 'tool_calls', usage(2000, 0)
        text = json.dumps({'status': 'complete', 'comments': []})
        return dict(content=text), 'stop', usage(2000, len(text))
    return respond


def scenario_immutable_skill(offline):
    """POD frozen-snapshot immutability: without hardening an unfenced bash
    write into the bound Skill directory lands there (the leancode-review
    bytecode-cache batch kill); after stage() hardening (chmod a-w) the same
    write fails at the OS layer on Linux and the post-run integrity check stays
    green. win32 bundles tool-bash as disabled, so that branch proves only the
    verification stay-green."""
    from reviewers.dsh_skill.selection import harden_snapshot, verify_after_execution
    work = build_work()
    record = _make_skill_snapshot(work)
    cfg = dict(skill_mode='skill', skill_version=record['id'], skill_identity=record,
               dsh_policy={'runtime_mode': 'pod'})
    leak = work / 'skill-version' / 'content' / 'leak.txt'
    try:
        if os.name == 'nt':
            verify_after_execution(cfg, work)
            print('  PASS immutable (win32): post-run integrity green; tool-bash is '
                  'disabled on win32, the write path is covered by unit tests and Linux runs')
            return PASS
        control = MockModel(bash_writer(runtime.locations(work)))
        try:
            policy_path = launch_policy(offline / 'node_modules', work, control.port, skill=True)
            control_record = run_dsh(offline, work, policy_path)
        finally:
            control.close()
        if not leak.exists():
            print(f'  FAIL immutable-control: unfenced bash should have created {leak} '
                  f'(exit={control_record.returncode} stderr={control_record.stderr[-300:]})')
            return FAIL
        leak.unlink()
        harden_snapshot(work / 'skill-version')
        hardened = MockModel(bash_writer(runtime.locations(work)))
        try:
            policy_path = launch_policy(offline / 'node_modules', work, hardened.port, skill=True)
            hardened_record = run_dsh(offline, work, policy_path)
        finally:
            hardened.close()
        if leak.exists():
            print(f'  FAIL immutable-hardened: bash write into the hardened snapshot '
                  f'succeeded (exit={hardened_record.returncode})')
            return FAIL
        verify_after_execution(cfg, work)
        print('  PASS immutable: control bash write into the frozen Skill dir landed; '
              'after hardening it was blocked at the OS layer and post-run '
              'integrity stayed green (control exit %d, hardened exit %d)'
              % (control_record.returncode, hardened_record.returncode))
        return PASS
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline-root',
                        default=os.environ.get('DSH_OFFLINE_ROOT', r'D:\devSoftware\dsh'))
    parser.add_argument('--scenario', choices=['all', 'compaction', 'headers', 'write', 'scope', 'immutable'])
    args = parser.parse_args()
    offline = Path(args.offline_root)
    if not (offline / 'node_modules/@deepseek-ai/dsh/lib/bin.js').is_file():
        raise SystemExit(f'offline DSH install not found at {offline}')
    scenarios = [('compaction', scenario_compaction), ('headers', scenario_headers),
                 ('write', scenario_write_scope), ('scope', scenario_scope),
                 ('immutable', scenario_immutable_skill)]
    status = PASS
    for name, run in scenarios:
        if args.scenario not in (None, 'all', name):
            continue
        print(f'== scenario: {name} ==')
        status |= run(offline)
    print('RESULT:', 'PASS' if status == PASS else 'FAIL')
    raise SystemExit(status)


if __name__ == '__main__':
    main()
