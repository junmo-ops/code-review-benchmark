"""Prepare, verify and install the pinned DSH offline inputs; never call a model."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

from reviewers.dsh_skill.runtime import RUNTIME
from reviewers.dsh_skill.runtime import runtime_mode
from .dsh_artifacts import digest, verify
from . import dsh_npm, dsh_system, dsh_archive
from .dsh_preflight import enabled


def verify_components(bundle, component):
    components = ['npm', 'system'] if component == 'all' else [component]
    for name in components:
        verify(bundle, name)
    return {name: digest(bundle / f'{name}.json') for name in components}


def stage(bundle, destination, component):
    identity = verify_components(bundle, component)
    if destination.exists():
        raise ValueError('staging destination already exists; do not overwrite build inputs')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dsh-stage-', dir=destination.parent) as directory:
        staging = Path(directory) / 'payload'
        staging.mkdir()
        for name in identity:
            shutil.copyfile(bundle / f'{name}.json', staging / f'{name}.json')
            folder = 'npm-cache' if name == 'npm' else 'rpms'
            shutil.copytree(bundle / folder, staging / folder)
        verify_components(staging, component)
        staging.rename(destination)
    print(json.dumps(dict(staged=str(destination), components=identity,
                          complete=component == 'all')), flush=True)


def execute(args):
    if args.command == 'install' and args.if_enabled and not enabled():
        print('DSH disabled; offline inputs not installed.', flush=True)
        return
    with dsh_archive.open_bundle(args.bundle.resolve()) as bundle:
        _execute(args, bundle)


def _execute(args, bundle):
    if args.command == 'verify':
        print(json.dumps(verify_components(bundle, args.component)), flush=True)
    elif args.command == 'prepare-npm':
        dsh_npm.prepare(bundle)
    elif args.command == 'prepare-system':
        dsh_system.prepare(bundle, args.base_image, args.rpm_source)
    elif args.command == 'install-system':
        dsh_system.install(bundle)
    elif args.command == 'stage':
        if args.destination is None:
            raise ValueError('--destination is required for stage')
        stage(bundle, args.destination.resolve(), args.component)
    elif args.command == 'pack-npm':
        if args.destination is None:
            raise ValueError('--destination is required for pack-npm')
        dsh_archive.pack(bundle, args.destination.resolve())
    elif args.command == 'install':
        verify_components(bundle, 'all' if runtime_mode() == 'sandbox' else 'npm')
        if runtime_mode() == 'sandbox':
            dsh_system.install(bundle)
        dsh_npm.install(bundle, args.destination.resolve() if args.destination else RUNTIME)
    else:
        if args.destination is None:
            raise ValueError('--destination is required for install-npm')
        dsh_npm.install(bundle, args.destination.resolve())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare-npm', 'prepare-system', 'install-npm', 'install-system',
                                          'install', 'verify', 'stage', 'pack-npm'])
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--component', choices=['npm', 'system', 'all'], default='all')
    parser.add_argument('--base-image')
    parser.add_argument('--rpm-source')
    parser.add_argument('--if-enabled', action='store_true')
    args = parser.parse_args()
    try:
        execute(args)
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError) as error:
        print(f'DSH offline: {error}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
