"""Target-only RPM preparation and offline installation, with no privilege fallback."""
import os
from pathlib import Path
import platform
import re
import shutil
import stat

from .dsh_artifacts import digest, verify, write_manifest
from .dsh_npm import check_platform, run


def target_identity():
    check_platform()
    release = platform.freedesktop_os_release()
    if release.get('ID', '').lower() != 'kylin' or not re.fullmatch(r'v?10(?:\..*)?', release.get('VERSION_ID', '').lower()):
        raise ValueError('system artifacts require the actual C86/Kylin 10 target image; Ubuntu binaries are not accepted')
    return dict(os_release_sha256=digest('/etc/os-release'), architecture=platform.machine(),
                glibc=list(platform.libc_ver()), release=release)


def package_identity(path):
    architecture = run(['rpm', '-qp', '--qf', '%{ARCH}', str(path)])
    if architecture not in {'x86_64', 'noarch'}:
        raise ValueError('RPM architecture mismatch: ' + str(path) + ' (' + architecture + ')')
    signature = run(['rpmkeys', '--checksig', '--verbose', str(path)])
    if not re.search(r'signature.*: OK', signature, re.IGNORECASE) or re.search(r'NOKEY|NOT OK', signature):
        raise ValueError('RPM signature not trusted by target keyring: ' + str(path))
    identity = dict(architecture=architecture,
                    nevra=run(['rpm', '-qp', '--qf', '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}', str(path)]),
                    requires=run(['rpm', '-qp', '--requires', str(path)]))
    return identity, signature


def prepare(bundle, base_image, source):
    target = target_identity()
    if not re.fullmatch(r'csbase\.registry\.cmbchina\.cn/paas/cmb-python-3\.12@sha256:[a-f0-9]{64}', base_image or ''):
        raise ValueError('--base-image must be the approved Python target image at an inspected sha256 digest')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)*', source or ''):
        raise ValueError('--rpm-source must list approved Kylin snapshot repository IDs (comma-separated)')
    if (bundle / 'rpms').exists() or (bundle / 'system.json').exists():
        raise ValueError('system artifact destination already exists; use a fresh directory')
    rpms = bundle / 'rpms'
    rpms.mkdir(parents=True)
    run(['yumdownloader', '--disablerepo=*', '--enablerepo=' + source,
         '--resolve', '--alldeps', '--destdir=' + str(rpms), 'bubblewrap'])
    inspected = {path.name: package_identity(path) for path in sorted(rpms.glob('*.rpm'))}
    packages = {name: result[0] for name, result in inspected.items()}
    if not any(value['nevra'].startswith('bubblewrap-') for value in packages.values()):
        raise ValueError('resolved RPM bundle is missing Bubblewrap')
    write_manifest(bundle, 'system', system=target, base_image=base_image, rpm_source=source,
                   packages=packages, signature_diagnostics={name: result[1] for name, result in inspected.items()})
    print('Prepared target RPMs; offline dependency resolution and runtime namespace checks are still required.', flush=True)


def bubblewrap_identity():
    binary = shutil.which('bwrap')
    if not binary:
        raise ValueError('missing Bubblewrap; refusing unsandboxed execution')
    info = Path(binary).stat()
    if info.st_mode & (stat.S_ISUID | stat.S_ISGID):
        raise ValueError('setuid/setgid Bubblewrap is not accepted; no privilege fallback')
    try:
        capabilities = os.getxattr(binary, 'security.capability')
    except OSError as error:
        if error.errno not in {61, 95}:
            raise
        capabilities = b''
    if capabilities:
        raise ValueError('file-capability Bubblewrap is not accepted; no privilege fallback')
    return dict(version=run([binary, '--version']), sha256=digest(binary))


def install(bundle):
    manifest = verify(bundle, 'system')
    actual = target_identity()
    if manifest.get('system') != actual:
        raise ValueError('system ABI/base release mismatch; prepare RPMs in the exact target base')
    paths = sorted((bundle / 'rpms').glob('*.rpm'))
    # rpmkeys diagnostics include the input path; keep them out of relocatable package identity.
    packages = {path.name: package_identity(path)[0] for path in paths}
    if not paths or packages != manifest.get('packages'):
        raise ValueError('RPM package identity mismatch')
    if not any(value['nevra'].startswith('bubblewrap-') for value in packages.values()):
        raise ValueError('RPM bundle is missing Bubblewrap')
    run(['yum', '--disablerepo=*', '--setopt=localpkg_gpgcheck=1', '--setopt=gpgcheck=1',
         'install', '-y', *map(str, paths)])
    print(bubblewrap_identity(), flush=True)
