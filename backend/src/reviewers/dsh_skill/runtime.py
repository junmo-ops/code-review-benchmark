"""Pinned Linux runtime with explicit sandbox or shared-POD execution."""
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import tempfile

from runner.paths import PROJECT_ROOT

VERSION = '0.2.1-rc.2'
RUNTIME = PROJECT_ROOT / 'dependencies/dsh'
MODULES = RUNTIME / 'node_modules'
ENTRY = MODULES / '@deepseek-ai/dsh/lib/bin.js'
POLICY = dict(request_retries=0, compaction_retries=0, overflow_retries=0,
              compaction_failure='abort_turn_no_recovery', context_window=131072, max_output_tokens=8192,
              compaction='fixed_default_threshold', tools=['read', 'glob', 'grep'],
              tool_read_scope='case_and_task_input_only',
              isolation='bubblewrap_pid_mount_network', model_transport='fixed_endpoint_unix_relay')


def output_limit(value=8192):
    if type(value) is not int or not 1 <= value <= 65536:
        raise IsolationError('DSH 单次输出上限必须为 1–65536 的整数')
    return value


def policy(mode, max_output_tokens=8192):
    selected = runtime_mode()
    result = dict(POLICY, max_output_tokens=output_limit(max_output_tokens),
                  runtime_mode=selected, os_isolation=selected == 'sandbox')
    if selected == 'pod':
        result['isolation'] = 'pod_shared_process_software_guards_only'
    if mode == 'skill':
        result.update(tools=[*POLICY['tools'], 'skill'], tool_read_scope='case_task_and_bound_skill_only')
    return result


def runtime_mode():
    value = os.environ.get('BENCH_DSH_RUNTIME_MODE', 'sandbox')
    if value not in {'sandbox', 'pod'}:
        raise IsolationError('BENCH_DSH_RUNTIME_MODE must be sandbox or pod')
    return value


def locations(work):
    if runtime_mode() == 'pod':
        work = work.resolve()
        return dict(repo=str(work / 'repo'), input=str(work / 'input'), home=str(work / 'home'),
                    skill=str(work / 'skill-version/content'), socket=str(work / 'model.sock'))
    return dict(repo='/work', input='/input', home='/home/dsh', skill='/skill', socket='/model.sock')


def script(name):
    if runtime_mode() == 'pod':
        return str(PROJECT_ROOT / 'scripts/dsh' / name)
    return '/' + name


def environment(work):
    if runtime_mode() == 'pod':
        from .pod import environment as pod_environment
        return pod_environment(work)
    return {'PATH': os.defpath}


class IsolationError(ValueError):
    """A system error that must stop the batch, not a failed individual answer."""


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def node():
    value = shutil.which('node')
    if not value:
        raise IsolationError('缺少 Node，不能启动 Linux DSH')
    return Path(value).resolve()


def identity():
    if os.environ.get('BENCH_DSH_ENABLED') == '0':
        raise IsolationError('DSH disabled in this image (BENCH_DSH_ENABLED=0)')
    if platform.system() != 'Linux' or not ENTRY.is_file():
        raise IsolationError('需要 Linux 固定版 DSH；先运行 npm ci --prefix dependencies/dsh')
    package = json.loads((ENTRY.parent.parent / 'package.json').read_text())
    if package['version'] != VERSION:
        raise IsolationError('DSH 版本不是固定的 ' + VERSION)
    actual = subprocess.check_output([str(node()), str(ENTRY), '--version'], text=True, timeout=20).strip()
    if actual != VERSION:
        raise IsolationError('DSH 实际版本不匹配')
    node_version = subprocess.check_output([str(node()), '--version'], text=True, timeout=10).strip()
    if tuple(int(part) for part in node_version.lstrip('v').split('.')) < (22, 19, 0):
        raise IsolationError('此 DSH 锁定依赖要求 Node >=22.19.0')
    sources = sorted(Path(__file__).parent.glob('*.py')) + [PROJECT_ROOT / 'scripts/dsh' / name
                                                          for name in ('bridge.mjs', 'compaction.mjs', 'scope.mjs', 'candidate.mjs')]
    distribution = {}
    if (RUNTIME / 'headless-distribution.json').exists():
        from deployment.dsh_release_validation import validate_installed
        distribution = validate_installed(RUNTIME)
        if not node_version.startswith('v22.22.'):
            raise IsolationError('Headless DSH runtime requires Node 22.22.x')
        sources += [PROJECT_ROOT / 'backend/src/deployment/dsh_release_validation.py',
                    RUNTIME / 'headless-release.json']
    return dict(version=actual, runtime_mode=runtime_mode(), os_isolation=runtime_mode() == 'sandbox',
                distribution=distribution,
                package_lock_sha256=sha(RUNTIME / 'package-lock.json'),
                node_version=node_version,
                node_sha256=sha(node()), installed_sha256=installed_identity(),
                adapter_files={str(p.relative_to(PROJECT_ROOT)): sha(p) for p in sources})


def installed_identity():
    digest = hashlib.sha256()
    for path in sorted(MODULES.rglob('*')):
        if path.is_file() and '.bin' not in path.parts:
            digest.update(path.relative_to(MODULES).as_posix().encode())
            digest.update(sha(path).encode())
    return digest.hexdigest()


def namespace():
    binary = shutil.which('bwrap')
    if not binary:
        raise IsolationError('缺少 Bubblewrap，拒绝裸跑 DSH')
    args = [binary, '--unshare-all', '--die-with-parent', '--new-session']
    for path in ('/usr', '/bin', '/lib', '/lib64'):
        if Path(path).exists():
            args += ['--ro-bind', path, path]
    return args + ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
                   '--tmpfs', '/home', '--setenv', 'PATH', '/usr/bin:/bin']


def probe():
    if runtime_mode() == 'pod':
        from .pod import probe as pod_probe
        pod_probe()
        return
    _probe_namespace()


def _probe_namespace():
    namespaces = {name: str(Path('/proc/self/ns', name).readlink()) for name in ('user', 'mnt', 'pid', 'net')}
    with tempfile.TemporaryDirectory(prefix='dsh-isolation-probe-') as directory:
        root = Path(directory)
        (root / 'case').mkdir()
        (root / 'hidden').write_text('must-not-be-readable')
        script = '''
import fs from 'node:fs';
import net from 'node:net';
const parentNamespaces = JSON.parse(process.argv[2]);
for (const [name, parent] of Object.entries(parentNamespaces)) {
  if (fs.readlinkSync('/proc/self/ns/' + name) === parent) process.exit(73);
}
let denied = false;
try { fs.writeFileSync('/case/forbidden', 'x'); } catch { denied = true; }
if (!denied || fs.existsSync(process.argv[1])) process.exit(70);
const socket = net.connect({ host: '1.1.1.1', port: 443 });
socket.on('connect', () => process.exit(71));
socket.on('error', () => process.exit(0));
setTimeout(() => process.exit(72), 3000);
'''
        args = namespace() + ['--ro-bind', str(node()), '/node', '--ro-bind', str(root / 'case'), '/case',
                              '/node', '--input-type=module', '-e', script, str(root / 'hidden'), json.dumps(namespaces)]
        try:
            result = subprocess.run(args, capture_output=True, timeout=10, env={'PATH': os.defpath})
        except (OSError, subprocess.SubprocessError):
            raise IsolationError('Linux 隔离探测失败，拒绝运行 DSH') from None
    if result.returncode:
        details = result.stderr.decode('utf-8', errors='replace')[-2000:]
        raise IsolationError('Linux 命名空间隔离不可用，拒绝运行 DSH: ' + details)


def command(work, prompt):
    if runtime_mode() == 'pod':
        from .pod import command as pod_command
        return pod_command(work, prompt)
    skill = ['--ro-bind', str(work / 'skill-version/content'), '/skill'] if (work / 'skill-version').exists() else []
    return namespace() + skill + [
        '--ro-bind', str(node()), '/node', '--ro-bind', str(MODULES), '/runtime/node_modules',
        '--ro-bind', str(PROJECT_ROOT / 'scripts/dsh/bridge.mjs'), '/bridge.mjs',
        '--ro-bind', str(PROJECT_ROOT / 'scripts/dsh/compaction.mjs'), '/compaction.mjs',
        '--ro-bind', str(PROJECT_ROOT / 'scripts/dsh/scope.mjs'), '/scope.mjs',
        '--ro-bind', str(PROJECT_ROOT / 'scripts/dsh/candidate.mjs'), '/candidate.mjs',
        '--ro-bind', str(work / 'repo'), '/work',
        '--ro-bind', str(work / 'input'), '/input',
        '--bind', str(work / 'home'), '/home/dsh',
        '--ro-bind', str(work / 'model.sock'), '/model.sock',
        '--chdir', '/work', '--setenv', 'HOME', '/home/dsh',
        '--setenv', 'DSH_HOME', '/home/dsh', '--setenv', 'DSH_TELEMETRY_DISABLED', '1',
        '--setenv', 'BENCH_DSH_KEY', 'relay-placeholder',
        '/node', '--import', '/bridge.mjs', '/runtime/node_modules/@deepseek-ai/dsh/lib/bin.js',
        '--profile', 'headless', '--patch', '/input/policy.json', '--json', prompt]
