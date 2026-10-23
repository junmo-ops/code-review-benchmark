"""Shared-POD execution: software guards, never an OS isolation claim."""
import json
import os
from pathlib import Path
import secrets
import tempfile
import time

from . import runtime


def environment(work):
    paths = runtime.locations(work)
    return dict(PATH=os.defpath, HOME=paths['home'], DSH_HOME=paths['home'],
                DSH_TELEMETRY_DISABLED='1', BENCH_DSH_KEY=secrets.token_urlsafe(32),
                BENCH_DSH_SOCKET=paths['socket'], BENCH_DSH_RUNTIME=str(runtime.RUNTIME),
                BENCH_DSH_POLICY_INPUT=str(work / 'input/policy.json'),
                BENCH_DSH_POLICY_OUTPUT=str(work / 'launch-policy.json'))


def command(work, prompt):
    return [str(runtime.node()), '--import', runtime.script('bridge.mjs'), str(runtime.ENTRY),
            '--profile', 'headless', '--patch', str(work / 'launch-policy.json'), '--json', prompt]


def probe():
    from .process_tree import subreaper
    with subreaper(), tempfile.TemporaryDirectory(prefix='dsh-pod-preflight-') as directory:
        root = Path(directory)
        for name in ('repo', 'input', 'home', 'skill-version/content'):
            (root / name).mkdir(parents=True)
        (root / 'repo/allowed').write_text('case data')
        (root / 'repo/escape').symlink_to('/etc')
        _probe_guards(root)


def _probe_guards(root):
    from .supervisor import execute, evidence_path
    script = '''
const { apply } = await import(process.argv[1]);
const paths = JSON.parse(process.argv[2]);
let guard;
apply({ tools: { guard(fn) { guard = fn; } }, provide() {} }, paths);
const check = (name, args) => guard({ name, arguments: args });
if (check('read', { file_path: 'allowed' })) process.exit(71);
for (const target of ['/etc/os-release', '../input/../../etc/os-release', 'escape/os-release']) {
  if (!String(check('read', { file_path: target })).includes('BENCH_DSH_SCOPE_DENIED')) process.exit(72);
}
for (const name of ['write', 'edit', 'bash', 'web_fetch', 'subagent']) {
  if (!check(name, { file_path: 'allowed' })) process.exit(73);
}
'''
    command = [str(runtime.node()), '--input-type=module', '-e', script,
               runtime.script('scope.mjs'), json.dumps(runtime.locations(root))]
    record = execute(command, root, root / 'probe', time.monotonic() + 10, str)
    if record['exit_code']:
        error = evidence_path(root / 'probe', '.stderr.txt').read_text()
        raise runtime.IsolationError('DSH POD 软件路径/工具限制预检失败：' + error[-2000:])
