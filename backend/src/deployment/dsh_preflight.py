"""Separate dependency/ABI build checks from actual runtime namespace checks."""
import argparse
import json
import os
import platform
import subprocess
import sys

from reviewers.dsh_skill import runtime
from .dsh_npm import check_host, check_install
from .dsh_system import bubblewrap_identity


def enabled():
    value = os.environ.get('BENCH_DSH_ENABLED', '0')
    if value not in {'0', '1'}:
        raise ValueError('BENCH_DSH_ENABLED must be 0 or 1')
    return value == '1'


def check_build():
    check_host()
    mode = runtime.runtime_mode()
    result = dict(stage='build', status='installed', runtime_namespace='not_tested',
                  runtime_mode=mode, os_isolation=mode == 'sandbox',
                  dsh=check_install(runtime.RUNTIME),
                  platform=platform.platform(), glibc=platform.libc_ver(), uid=os.getuid())
    if mode == 'sandbox':
        result['bubblewrap'] = bubblewrap_identity()
    else:
        result.update(runtime_namespace='not_applicable',
                      warning='POD shared-process mode: software guards only, no OS sandbox')
    print(json.dumps(result), flush=True)
    return result


def check_runtime():
    result = check_build()
    runtime.identity()
    runtime.probe()
    result.update(stage='runtime', status='ready',
                  runtime_namespace='passed' if runtime.runtime_mode() == 'sandbox' else 'not_applicable',
                  software_guards='passed')
    print(json.dumps(result), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    stages = parser.add_mutually_exclusive_group(required=True)
    stages.add_argument('--check-build', action='store_true')
    stages.add_argument('--check-runtime', action='store_true')
    parser.add_argument('--if-enabled', action='store_true')
    args = parser.parse_args()
    try:
        if args.if_enabled and not enabled():
            print('DSH disabled (BENCH_DSH_ENABLED=0); existing non-DSH workflows remain available.')
            return 0
        if args.check_build:
            check_build()
        else:
            check_runtime()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'DSH preflight failed; no unsandboxed/privileged fallback: {error}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
