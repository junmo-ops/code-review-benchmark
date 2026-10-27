"""Prepare audited plaintext inputs from a fresh verified offline installation."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SUPPLIERS = ['@koromix/koffi-linux-x64', '@vscode/ripgrep-linux-x64',
             '@img/sharp-linux-x64', '@img/sharp-libvips-linux-x64']
COMPILED = {'@deepseek-ai/node-addon-system-linux-x64', 'node-addon-require-builtin-linux-x64-gnu'}
OMIT = re.compile(r'^(@deepseek-ai/(dsh-(client-|host-|web-app$|terminal|tool-terminal$|api-terminal-controller$|'
                  r'office-|skill-office$|experimental-.*(voice|speech|terminal|inspector))|libreoffice-kit)|'
                  r'node-pty$|@xterm/|sherpa-onnx|@img/sharp-wasm32$)')
ASSETS = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf', '.ttf', '.woff', '.woff2'}
NATIVE_COMMIT = 'a58d3599a2e551d7d706d1ff796c9fd4bbaff63c'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def target(values, actual):
    return not values or ('!' + actual not in values and
                          (actual in values or all(value.startswith('!') for value in values)))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')


def copy_text(source, destination, omitted):
    if source.is_symlink():
        raise ValueError('Source symlink: ' + str(source))
    data = source.read_bytes()
    try:
        data.decode('utf-8')
        if b'\0' in data or data.startswith((b'\x7fELF', b'PK\x03\x04', b'\x1f\x8b')):
            raise UnicodeError('NUL byte')
    except UnicodeError:
        if source.suffix.lower() in ASSETS:
            omitted.append(source.name + ': non-runtime graphical/document asset')
            return
        raise ValueError('Unaccounted binary input: ' + str(source)) from None
    if source.suffix.lower() in {'.node', '.so', '.dll', '.exe', '.wasm', '.gz', '.tgz', '.zip', '.7z', '.rpm'}:
        raise ValueError('Forbidden Git input suffix: ' + str(source))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def skip_package_file(relative, name):
    if relative.name in {'.npmignore', '.gitignore', 'package-lock.json'}:
        return True
    if 'node_modules' in relative.parts:
        return True
    if any(part in {'test', 'tests', 'testdata', 'fixtures', 'examples', 'benchmarks', '.github'} for part in relative.parts):
        return True
    if name in SUPPLIERS:
        return len(relative.parts) != 1 or relative.suffix not in {'.json', '.js', '.cjs', '.mjs', '.md', '.txt', ''}
    return name in COMPILED and relative.parts[0] in {'bin', 'prebuilt'}


def copy_package(folder, output, name, omitted):
    for file in sorted(folder.rglob('*')):
        relative = file.relative_to(folder)
        if file.is_symlink():
            raise ValueError('Source symlink: ' + str(file))
        if file.is_dir() or skip_package_file(relative, name):
            continue
        copy_text(file, output / relative, omitted)


def prepare_modules(runtime, vendor, lock, selected):
    modules, omitted, notices = [], [], []
    for location, package in sorted(lock['packages'].items()):
        if not location or package.get('link') or package.get('dev'):
            continue
        if location not in selected:
            continue
        if not all(target(package.get(key), value) for key, value in [('os', 'linux'), ('cpu', 'x64'), ('libc', 'glibc')]):
            continue
        name = location.split('node_modules/')[-1]
        if OMIT.match(name):
            omitted.append(name + ': unsupported GUI/voice/Office/interactive capability')
            continue
        folder = runtime / location
        metadata = json.loads((folder / 'package.json').read_text())
        if metadata['version'] != package['version']:
            raise ValueError('Installed version mismatch: ' + location)
        slot = hashlib.sha256(location.encode()).hexdigest()[:24]
        output = vendor / 'npm' / slot
        copy_package(folder, output, name, omitted)
        modules.append(dict(name=name, version=package['version'], location=location,
                            source='vendor/npm/' + slot, payload='dist/packages/' + slot,
                            upstream_integrity=package['integrity']))
        notices.append(f"{name}@{package['version']} | license={metadata.get('license', 'SEE PACKAGE LICENSE')} | {package.get('resolved', '')}")
    return modules, omitted, notices


def prepare_native(args, vendor):
    commit = subprocess.check_output(['git', '-C', str(args.native), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != NATIVE_COMMIT:
        raise ValueError('Unexpected native loader source commit')
    copied = []
    native_files = subprocess.check_output(['git', '-C', str(args.native), 'ls-files', '-z',
                                             '--', 'packages/native/src']).decode().split('\0')
    for name in filter(None, native_files):
        expected = subprocess.check_output(['git', '-C', str(args.native), 'show', 'HEAD:' + name])
        source = args.native / name
        if source.read_bytes() != expected:
            raise ValueError('Native source differs from pinned Git commit: ' + name)
        copy_text(source, vendor / 'native-loader' / source.relative_to(args.native / 'packages/native/src'), copied)
    for name in ['node_api.h', 'node_api_types.h', 'js_native_api.h', 'js_native_api_types.h', 'node_version.h']:
        copy_text(args.headers / name, vendor / 'node-headers' / name, copied)
    for file in sorted(args.api.iterdir()):
        if file.is_file() and (file.suffix == '.h' or file.name in {'package.json', 'LICENSE.md', 'README.md'}):
            copy_text(file, vendor / 'node-addon-api' / file.name, copied)
    copy_text(args.native / 'LICENSE', vendor / 'native-loader/LICENSE', copied)
    copy_text(args.node_license, vendor / 'node-headers/LICENSE', copied)
    if copied:
        raise ValueError('Unexpected non-source assets in native source inputs')
    if json.loads((args.api / 'package.json').read_text())['version'] != '8.9.0':
        raise ValueError('Build header version must be node-addon-api 8.9.0')
    return sorted(str(file.relative_to(vendor / 'native-loader')) for file in (vendor / 'native-loader').rglob('*.cc')
                  if file.name != 'runtime_compat_nodeabi.cc')


def supplier_inputs(vendor, lock, suppliers):
    dependencies = {name: lock['packages']['node_modules/' + name]['version'] for name in suppliers}
    manifest = dict(name='dsh-review-native-inputs', version='1.0.0', private=True, dependencies=dependencies)
    packages = {'': manifest}
    for name in suppliers:
        item = dict(lock['packages']['node_modules/' + name])
        for key in ['optional', 'dev', 'devOptional']:
            item.pop(key, None)
        packages['node_modules/' + name] = item
    directory = vendor / 'native-inputs'
    directory.mkdir()
    write_json(directory / 'package.json', manifest)
    write_json(directory / 'package-lock.json', dict(name=manifest['name'], version='1.0.0',
                                                   lockfileVersion=3, requires=True, packages=packages))


def supplier_files(runtime, suppliers):
    result = {}
    for name in suppliers:
        for file in sorted((runtime / 'node_modules' / name).rglob('*')):
            if file.is_symlink():
                raise ValueError('Supplier symlink in pristine input')
            if file.is_file():
                result[str(file.relative_to(runtime))] = digest(file)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--native', type=Path, required=True)
    parser.add_argument('--headers', type=Path, required=True)
    parser.add_argument('--api', type=Path, required=True)
    parser.add_argument('--node-license', type=Path, required=True)
    parser.add_argument('--destination', type=Path, default=ROOT / 'dependencies/dsh-review-source')
    args = parser.parse_args()
    if args.runtime.resolve() == ROOT / 'dependencies/dsh':
        raise ValueError('Do not vendor a development node_modules; use a fresh verified offline installation')
    receipt = json.loads((args.runtime / 'offline-install.json').read_text())
    lockfile = args.runtime / 'package-lock.json'
    if receipt.get('locked_packages') != 615 or digest(lockfile) != digest(ROOT / 'dependencies/dsh/package-lock.json'):
        raise ValueError('Pristine runtime does not match approved locked installation')
    if (args.destination / 'vendor').exists() or (args.destination / 'SOURCE_PLAN.json').exists():
        raise ValueError('Source inputs already exist; do not overwrite a reviewed snapshot')
    with tempfile.TemporaryDirectory(prefix='.dsh-source-', dir=args.destination.parent) as temporary:
        stage = Path(temporary)
        vendor = stage / 'vendor'
        vendor.mkdir()
        lock = json.loads(lockfile.read_text())
        closure = json.loads(subprocess.check_output(['node', str(ROOT / 'scripts/dsh/source-closure.mjs'),
                            '--runtime', str(args.runtime), '--omit', OMIT.pattern], text=True))
        modules, omitted, notices = prepare_modules(args.runtime, vendor, lock, set(closure['selected']))
        omitted.extend(closure['omitted'])
        native = prepare_native(args, vendor)
        names = {module['name'] for module in modules}
        suppliers = [name for name in SUPPLIERS if name in names]
        supplier_inputs(vendor, lock, suppliers)
        plan = dict(schema=1, kind='dsh-review-headless', upstreamVersion='0.2.1-rc.2',
                    target=dict(os='linux', cpu='x64', libc='glibc'),
                    capabilities=dict(headless=True, native_skill=True, web_ui=False, voice=False, office=False),
                    upstreamLockSha256=digest(lockfile), nativeSourceCommit=NATIVE_COMMIT,
                    modules=modules, suppliers=suppliers, nativeSources=native, omissions=omitted,
                    supplierFiles=supplier_files(args.runtime, suppliers),
                    files={str(file.relative_to(stage)): digest(file) for file in sorted(vendor.rglob('*')) if file.is_file()})
        vendor.rename(args.destination / 'vendor')
        write_json(args.destination / 'SOURCE_PLAN.json', plan)
        (args.destination / 'THIRD_PARTY_NOTICES.txt').write_text(
            'Runtime source snapshot; each vendored package retains its license files.\n'
            'Native loader MIT source commit: ' + NATIVE_COMMIT + '\n'
            'Node public headers 22.22.0; node-addon-api headers 8.9.0.\n\n' + '\n'.join(notices) + '\n')
        print(json.dumps(dict(modules=len(modules), source_files=len(plan['files']), omissions=len(omitted))))


if __name__ == '__main__':
    main()
