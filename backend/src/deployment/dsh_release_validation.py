"""Validate the approved source-built distribution without trusting its own inventory alone."""
import json
from pathlib import Path, PurePosixPath

from runner.paths import PROJECT_ROOT
from .dsh_artifacts import TARGET, digest, inventory

COMPILED = {
    'node_modules/node-addon-require-builtin-linux-x64-gnu/prebuilt/linux-x64-gnu-napi-v9.node',
    'node_modules/@deepseek-ai/node-addon-system-linux-x64/bin/glibc/system.node',
    'node_modules/@deepseek-ai/node-addon-system-linux-x64/bin/landlock-run',
}


def load(path):
    if path.is_symlink():
        raise ValueError('symlink release metadata')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('invalid release metadata')
    return value


def relative(name):
    path = PurePosixPath(name)
    if (not isinstance(name, str) or '\\' in name or path.is_absolute()
            or '..' in path.parts or str(path) != name or name in {'', '.'}):
        raise ValueError('unsafe release path')
    return name


def expected_release():
    value = load(PROJECT_ROOT / 'dependencies/dsh/headless-release.json')
    required = {'name', 'version', 'sourcePlanSha256', 'sourceFingerprint', 'generatedFiles', 'targetCheckSha256'}
    if not required <= value.keys() or not isinstance(value['generatedFiles'], dict):
        raise ValueError('malformed approved headless release configuration')
    return value


def metadata(manifest, plan_path, expected):
    plan = load(plan_path)
    if (digest(plan_path) != expected['sourcePlanSha256']
            or manifest.get('sourcePlanSha256') != expected['sourcePlanSha256']
            or manifest.get('sourceFingerprint') != expected['sourceFingerprint']
            or manifest.get('internalVersion') != expected['version']):
        raise ValueError('unapproved headless source plan, fingerprint or version')
    if (manifest.get('kind') != 'dsh-review-headless' or manifest.get('schema') != 1
            or manifest.get('target') != TARGET or manifest.get('modules') != plan['modules']
            or manifest.get('capabilities') != plan['capabilities']
            or plan.get('upstreamVersion') != '0.2.1-rc.2'
            or not isinstance(manifest.get('files'), dict) or not isinstance(manifest.get('toolchain'), dict)
            or plan.get('upstreamLockSha256') != digest(PROJECT_ROOT / 'dependencies/dsh/package-lock.json')):
        raise ValueError('headless release contract mismatch')
    return plan


def target_check(root, manifest, expected, filename):
    validation = manifest.get('runtimeValidation', {})
    if (not isinstance(validation, dict) or validation.get('requiredNode') != '22.22.x'
            or validation.get('status') != 'required-at-install'
            or manifest.get('targetCheckSha256') != expected['targetCheckSha256']
            or digest(root / filename) != expected['targetCheckSha256']):
        raise ValueError('missing/changed mandatory target runtime check')


def source_inventory(plan, expected):
    slots = {module['source']: module['location'] for module in plan['modules']}
    result = {}
    for name, sha in plan['files'].items():
        parts = name.split('/')
        slot = '/'.join(parts[:3])
        if slot in slots:
            result[relative(slots[slot] + '/' + '/'.join(parts[3:]))] = sha
    result.update(plan['supplierFiles'])
    result.update(expected['generatedFiles'])
    return result


def installed_files(manifest):
    slots = {}
    for module in manifest['modules']:
        location, payload = relative(module['location']), relative(module['payload'])
        if not location.startswith('node_modules/') or not payload.startswith('dist/packages/'):
            raise ValueError('invalid module location')
        if payload in slots or location in slots.values():
            raise ValueError('duplicate module location')
        slots[payload] = location
    result = {}
    for name, sha in manifest['files'].items():
        relative(name)
        parts = name.split('/')
        slot = '/'.join(parts[:3])
        if slot not in slots or len(parts) < 4:
            raise ValueError('file outside release modules')
        result[relative(slots[slot] + '/' + '/'.join(parts[3:]))] = sha
    return result


def approved_inventory(manifest, plan, expected):
    actual = installed_files(manifest)
    source = source_inventory(plan, expected)
    if set(actual) != set(source) | COMPILED:
        raise ValueError('headless inventory has missing or unapproved files')
    if any(actual[name] != sha for name, sha in source.items()):
        raise ValueError('headless source/supplier bytes changed')
    return actual


def validate_package(root, expected=None):
    expected = expected or expected_release()
    package = load(root / 'package.json')
    if (package.get('name') != expected['name'] or package.get('version') != expected['version']
            or package.get('dependencies') or package.get('optionalDependencies')
            or package.get('peerDependencies') or package.get('bundledDependencies')
            or any(name in package.get('scripts', {}) for name in ('preinstall', 'install', 'postinstall'))):
        raise ValueError('unexpected internal npm package identity or installation dependencies')
    manifest = load(root / 'dist/runtime-manifest.json')
    plan = metadata(manifest, root / 'dist/source-plan.json', expected)
    target_check(root, manifest, expected, 'dist/target-check.mjs')
    approved_inventory(manifest, plan, expected)
    if inventory(root, 'dist/packages') != manifest['files']:
        raise ValueError('headless payload inventory mismatch')
    return manifest


def validate_installed(root, expected=None):
    expected = expected or expected_release()
    manifest = load(root / 'headless-distribution.json')
    plan = metadata(manifest, root / 'headless-source-plan.json', expected)
    target_check(root, manifest, expected, 'headless-target-check.mjs')
    actual = approved_inventory(manifest, plan, expected)
    if inventory(root, 'node_modules') != actual:
        raise ValueError('installed headless inventory mismatch')
    return dict(kind=manifest['kind'], internal_version=manifest['internalVersion'],
                source_fingerprint=manifest['sourceFingerprint'],
                source_plan_sha256=manifest['sourcePlanSha256'],
                target_check_sha256=manifest['targetCheckSha256'],
                native_toolchain=manifest['toolchain'], locked_packages=len(plan['modules']))
