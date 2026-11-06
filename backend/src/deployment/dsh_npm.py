"""Install only locked npm contents; lifecycle scripts and online recovery stay off."""
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

from reviewers.dsh_skill.runtime import RUNTIME, VERSION
from .dsh_artifacts import TARGET, inputs, verify, write_manifest


def run(command, *, cwd=None, env=None, timeout=300):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ValueError(f'command failed ({result.returncode}): {command[0]}\n{result.stdout}\n{result.stderr}')
    return result.stdout.strip()


def check_platform():
    if platform.system() != 'Linux' or platform.machine() not in {'x86_64', 'amd64'}:
        raise ValueError('DSH offline target requires Linux x86_64, not this host')
    if platform.libc_ver()[0] != 'glibc':
        raise ValueError('DSH offline target requires glibc')


def check_host():
    check_platform()
    version = run(['node', '--version'])
    if tuple(map(int, version.lstrip('v').split('.'))) < (22, 19, 0):
        raise ValueError('DSH requires Node >=22.19.0')


def npm_install(prefix, cache, *, offline):
    env = dict(PATH=os.environ['PATH'], HOME=str(prefix), LANG='C.UTF-8',
               NPM_CONFIG_USERCONFIG='/dev/null', NPM_CONFIG_GLOBALCONFIG=str(prefix / 'global.npmrc'))
    args = ['npm', 'ci', '--prefix', str(prefix), '--cache', str(cache), '--ignore-scripts',
            '--include=optional', '--engine-strict', '--no-audit', '--no-fund',
            '--registry=https://registry.npmjs.org/', '--fetch-retries=0']
    if offline:
        args.append('--offline')
    print(run(args, env=env), flush=True)


def copy_inputs(prefix):
    inputs()
    for name in ('package.json', 'package-lock.json'):
        shutil.copyfile(RUNTIME / name, prefix / name)


def matches(values, actual):
    return not values or (('!' + actual) not in values and
                          (actual in values or all(value.startswith('!') for value in values)))


def check_install(prefix, *, expected_headless=None):
    if (prefix / 'headless-distribution.json').exists():
        from .dsh_release_validation import validate_installed
        release = validate_installed(prefix, expected_headless)
        if not run(['node', '--version']).startswith('v22.22.'):
            raise ValueError('Headless DSH runtime requires Node 22.22.x')
        with tempfile.TemporaryDirectory(prefix='dsh-target-env-') as home:
            env = dict(PATH=os.environ['PATH'], HOME=home, LANG='C.UTF-8')
            result = json.loads(run(['node', str(prefix / 'headless-target-check.mjs'), str(prefix)], env=env))
        if result.get('status') != 'passed' or not result.get('node', '').startswith('22.22.'):
            raise ValueError('Target runtime check did not pass')
        return dict(check_executables(prefix), target_validation=result, **release)
    return check_full_install(prefix)


def check_full_install(prefix):
    lock = json.loads((RUNTIME / 'package-lock.json').read_text())
    count = 0
    for path, package in lock['packages'].items():
        if not path or not all(matches(package.get(key), TARGET[key]) for key in ('os', 'cpu', 'libc')):
            continue
        installed = prefix / path / 'package.json'
        if not installed.is_file() or json.loads(installed.read_text())['version'] != package['version']:
            raise ValueError('missing/wrong-version locked dependency: ' + path)
        count += 1
    return dict(check_executables(prefix), locked_packages=count)


def check_executables(prefix):
    version = run(['node', str(prefix / 'node_modules/@deepseek-ai/dsh/lib/bin.js'), '--version'])
    if version != VERSION:
        raise ValueError('installed DSH version mismatch: ' + version)
    # Exercise the native grep binary used by the permitted read-only tools, not only JS metadata.
    script = "import {rgPath} from '@vscode/ripgrep'; import {execFileSync} from 'node:child_process'; console.log(execFileSync(rgPath,['--version'],{encoding:'utf8'}));"
    grep = run(['node', '--input-type=module', '-e', script], cwd=prefix)
    return dict(version=version, node=run(['node', '--version']), ripgrep=grep)


def prepare(bundle):
    check_host()
    if (bundle / 'npm-cache').exists() or (bundle / 'npm.json').exists():
        raise ValueError('npm artifact destination already exists; use a fresh bundle directory')
    bundle.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dsh-npm-prepare-') as directory:
        prefix = Path(directory)
        copy_inputs(prefix)
        npm_install(prefix, prefix / 'cache', offline=False)
        checked = check_install(prefix)
        shutil.copytree(prefix / 'cache/_cacache', bundle / 'npm-cache/_cacache')
    write_manifest(bundle, 'npm', preparation=checked)
    print('Prepared npm inputs only; Kylin RPMs and runtime namespaces are NOT certified.', flush=True)


def install(bundle, destination):
    verify(bundle, 'npm')
    check_host()
    if (destination / 'node_modules').exists():
        raise ValueError('destination node_modules already exists; refusing to replace it')
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dsh-install-', dir=destination) as directory:
        prefix = Path(directory)
        copy_inputs(prefix)
        shutil.copytree(bundle / 'npm-cache', prefix / 'cache')
        npm_install(prefix, prefix / 'cache', offline=True)
        checked = check_install(prefix)
        for name in ('package.json', 'package-lock.json'):
            target = destination / name
            if target.exists() and target.read_bytes() != (RUNTIME / name).read_bytes():
                raise ValueError('destination lock/manifest mismatch: ' + str(target))
        for name in ('package.json', 'package-lock.json'):
            shutil.copyfile(RUNTIME / name, destination / name)
        (prefix / 'node_modules').rename(destination / 'node_modules')
    (destination / 'offline-install.json').write_text(json.dumps(checked, indent=2) + '\n')
    print(json.dumps(checked), flush=True)
