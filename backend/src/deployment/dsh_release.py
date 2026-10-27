"""Fetch a fixed internal npm artifact, validate it, then restore its original module layout."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from urllib.parse import urlsplit

from reviewers.dsh_skill.runtime import RUNTIME
from .dsh_npm import check_host, check_install, copy_inputs
from .dsh_release_validation import expected_release, relative, validate_package


def unpack(archive, destination):
    names, total = set(), 0
    with tarfile.open(archive, 'r:gz') as handle:
        for member in handle:
            name = relative(member.name.rstrip('/'))
            if not name.startswith('package/') or name in names:
                raise ValueError('unsafe/duplicate internal npm archive path')
            if not (member.isdir() or member.isfile()) or member.sparse is not None:
                raise ValueError('non-regular internal npm archive member')
            names.add(name)
            total += member.size
            if member.size < 0 or total > 2 * 1024**3 or len(names) > 100000:
                raise ValueError('internal npm archive exceeds extraction limits')
            handle.extract(member, destination, filter='data')
    return destination / 'package'


def restore(package, destination, expected=None):
    manifest = validate_package(package, expected)
    if ((destination / 'node_modules').exists() or (destination / 'node_modules').is_symlink()
            or (destination / 'headless-distribution.json').exists()):
        raise ValueError('destination already contains a runtime; refusing replacement')
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.headless-install-', dir=destination) as directory:
        stage = Path(directory)
        copy_inputs(stage)
        for module in manifest['modules']:
            shutil.copytree(package / module['payload'], stage / module['location'])
        shutil.copyfile(package / 'dist/source-plan.json', stage / 'headless-source-plan.json')
        shutil.copyfile(package / 'dist/target-check.mjs', stage / 'headless-target-check.mjs')
        (stage / 'headless-distribution.json').write_text(json.dumps(manifest, indent=2) + '\n')
        checked = check_install(stage, expected_headless=expected)
        for name in ('package.json', 'package-lock.json'):
            target = destination / name
            if target.exists() and target.read_bytes() != (stage / name).read_bytes():
                raise ValueError('destination DSH lock/manifest mismatch')
        for name in ('package.json', 'package-lock.json', 'headless-source-plan.json',
                     'headless-distribution.json', 'headless-target-check.mjs'):
            shutil.copyfile(stage / name, destination / name)
        (stage / 'node_modules').rename(destination / 'node_modules')
    return checked


def registry_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('registry must be an HTTP(S) URL without credentials/query/fragment')
    return value


def npm_json(directory, registry, release, args):
    flags = ['--registry=' + registry, '--cache=' + str(directory / 'cache'),
             '--fetch-retries=0', '--fetch-timeout=30000', '--no-update-notifier']
    if release['name'].startswith('@'):
        flags.append('--' + release['name'].split('/')[0] + ':registry=' + registry)
    result = subprocess.run(['npm', *args, '--ignore-scripts', '--json', *flags],
                            cwd=directory, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise ValueError('internal npm fetch failed; inspect company registry permissions/version (no public fallback)')
    return json.loads(result.stdout)


def artifact_url(value, registry):
    registry_url(value)
    source, approved = urlsplit(value), urlsplit(registry)
    default_ports = {'http': 80, 'https': 443}
    source_port = source.port if source.port is not None else default_ports[source.scheme]
    approved_port = approved.port if approved.port is not None else default_ports[approved.scheme]
    if (source.scheme, source.hostname, source_port) != (approved.scheme, approved.hostname, approved_port):
        raise ValueError('internal npm metadata points outside the selected registry origin')
    return value


def fetch(directory, registry):
    release = expected_release()
    metadata = npm_json(directory, registry, release,
                        ['view', release['name'] + '@' + release['version'], 'version', 'dist'])
    if metadata['version'] != release['version']:
        raise ValueError('internal npm metadata version mismatch')
    url = artifact_url(metadata['dist']['tarball'], registry)
    # Fetch the checked corporate URL only, never resolve runtime dependencies or execute hooks.
    records = npm_json(directory, registry, release, ['pack', url])
    if len(records) != 1 or Path(records[0]['filename']).name != records[0]['filename']:
        raise ValueError('unexpected npm pack result')
    archive = directory / records[0]['filename']
    with archive.open('rb') as handle:
        integrity = 'sha512-' + base64.b64encode(hashlib.file_digest(handle, 'sha512').digest()).decode('ascii')
    if integrity != metadata['dist']['integrity']:
        raise ValueError('internal npm archive integrity mismatch')
    return unpack(archive, directory / 'unpacked')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', default=os.environ.get('NPM_CONFIG_REGISTRY',
                        'http://central.jaf.cmbchina.cn/artifactory/api/npm/group-npm/'))
    parser.add_argument('--package-dir', type=Path, help='already extracted artifact; local acceptance only')
    parser.add_argument('--destination', type=Path, default=RUNTIME)
    parser.add_argument('--if-enabled', action='store_true')
    args = parser.parse_args()
    try:
        from .dsh_preflight import enabled
        if args.if_enabled and not enabled():
            print('DSH disabled (BENCH_DSH_ENABLED=0); internal npm fetch skipped.')
            return 0
        check_host()
        with tempfile.TemporaryDirectory(prefix='dsh-internal-npm-') as directory:
            package = args.package_dir or fetch(Path(directory), registry_url(args.registry))
            checked = restore(package, args.destination.resolve())
        print(json.dumps(checked), flush=True)
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError) as error:
        print('Headless DSH install failed: ' + str(error))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
