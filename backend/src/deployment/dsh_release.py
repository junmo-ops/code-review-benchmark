"""Fetch a fixed internal npm artifact, validate it, then restore its original module layout."""
import argparse
import base64
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from reviewers.dsh_skill.runtime import RUNTIME
from .dsh_npm import check_host, check_install, copy_inputs
from .dsh_release_validation import expected_release, relative, validate_package

MAX_ARCHIVE_BYTES = 256 * 1024**2
DOWNLOAD_SECONDS = 180
SOCKET_TIMEOUT = 30


class NoArchiveRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Only the approved metadata URL may supply bytes, not an unchecked redirect target.
        return None


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


def _archive_length(response):
    length = response.headers.get('Content-Length')
    if length is None:
        return None
    if not length.isdecimal() or int(length) > MAX_ARCHIVE_BYTES:
        raise ValueError('invalid/oversized internal npm archive Content-Length')
    return int(length)


def _copy_archive(response, output, integrity, started):
    length = _archive_length(response)
    total, report_at = 0, 16 * 1024**2
    digest = hashlib.sha512()
    while chunk := response.read1(1024 * 1024):
        total += len(chunk)
        if time.monotonic() - started > DOWNLOAD_SECONDS:
            raise ValueError('internal npm archive download exceeded time budget')
        if total > MAX_ARCHIVE_BYTES:
            raise ValueError('internal npm archive exceeds download size limit')
        output.write(chunk)
        digest.update(chunk)
        if total >= report_at:
            print(f'Headless DSH: downloaded {total} bytes', file=sys.stderr, flush=True)
            report_at += 16 * 1024**2
    if length is not None and length != total:
        raise ValueError('internal npm archive download is truncated')
    actual = 'sha512-' + base64.b64encode(digest.digest()).decode('ascii')
    if actual != integrity:
        raise ValueError('internal npm archive integrity mismatch')
    return total


def download(directory, url, registry, integrity):
    url = artifact_url(url, registry)
    archive = directory / 'internal-runtime.tgz'
    if archive.exists():
        raise ValueError('internal npm archive destination already exists')
    started = time.monotonic()
    print('Headless DSH: downloading approved archive directly (no npm pack)', file=sys.stderr, flush=True)
    request = Request(url, headers={'Accept-Encoding': 'identity'})
    try:
        with tempfile.TemporaryDirectory(prefix='.archive-', dir=directory) as temporary:
            partial = Path(temporary) / 'runtime.tgz'
            with build_opener(NoArchiveRedirect()).open(request, timeout=SOCKET_TIMEOUT) as response:
                if response.status != 200 or response.headers.get('Content-Encoding', 'identity') != 'identity':
                    raise ValueError('unexpected internal npm archive HTTP response')
                with partial.open('xb') as output:
                    total = _copy_archive(response, output, integrity, started)
            partial.rename(archive)
    except HTTPError as error:
        error.close()
        raise ValueError(f'internal npm archive HTTP {error.code}; no npm credentials forwarded, '
                         'redirects and public fallback disabled') from None
    except (OSError, HTTPException):
        raise ValueError('internal npm archive network/read failed; no public fallback') from None
    print(f'Headless DSH: verified {total} archive bytes in {time.monotonic() - started:.1f}s',
          file=sys.stderr, flush=True)
    return archive


def fetch(directory, registry):
    release = expected_release()
    print('Headless DSH: reading fixed internal npm metadata', file=sys.stderr, flush=True)
    metadata = npm_json(directory, registry, release,
                        ['view', release['name'] + '@' + release['version'], 'version', 'dist'])
    if metadata['version'] != release['version']:
        raise ValueError('internal npm metadata version mismatch')
    url = artifact_url(metadata['dist']['tarball'], registry)
    archive = download(directory, url, registry, metadata['dist']['integrity'])
    print('Headless DSH: inspecting and extracting verified archive', file=sys.stderr, flush=True)
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
