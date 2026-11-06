"""Test-only inherited seccomp fault: namespaces denied, ordinary processes allowed."""
import ctypes
import ctypes.util
import errno
import shutil
import subprocess
import sys


class Comparison(ctypes.Structure):
    _fields_ = [('arg', ctypes.c_uint), ('op', ctypes.c_int),
                ('mask', ctypes.c_uint64), ('value', ctypes.c_uint64)]


def library():
    lib = ctypes.CDLL(ctypes.util.find_library('seccomp'))
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                         ctypes.c_uint, ctypes.POINTER(Comparison)]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    return lib


def add_rule(lib, context, name, error, comparison=None):
    number = lib.seccomp_syscall_resolve_name(name.encode())
    if number < 0:
        raise RuntimeError('test libseccomp does not recognize syscall ' + name)
    count = int(comparison is not None)
    pointer = ctypes.byref(comparison) if comparison is not None else None
    result = lib.seccomp_rule_add_array(context, 0x00050000 | error, number, count, pointer)
    if result != 0:
        raise RuntimeError('test seccomp rule failed: ' + name)


def deny_namespaces():
    # libseccomp API/struct constants: https://github.com/seccomp/libseccomp/blob/main/include/seccomp.h.in
    lib = library()
    context = lib.seccomp_init(0x7fff0000)
    if not context:
        raise RuntimeError('cannot allocate test seccomp filter')
    try:
        add_rule(lib, context, 'unshare', errno.EPERM)
        add_rule(lib, context, 'clone3', errno.ENOSYS)
        for flag in (0x00020000, 0x02000000, 0x04000000, 0x08000000,
                     0x10000000, 0x20000000, 0x40000000):
            add_rule(lib, context, 'clone', errno.EPERM, Comparison(0, 7, flag, flag))
        if lib.seccomp_load(context) != 0:
            raise RuntimeError('cannot load test seccomp filter')
    finally:
        lib.seccomp_release(context)


def check_denial():
    commands = [['unshare', '--user', '--map-root-user', '/bin/true']]
    if shutil.which('bwrap'):
        commands.append(['bwrap', '--unshare-user', '--ro-bind', '/', '/', '/bin/true'])
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            raise RuntimeError('namespace fault injection did not block ' + command[0])
        print('Test-only namespace denial: ' + command[0] + ': ' + result.stderr.strip(), file=sys.stderr)


if __name__ == '__main__':
    deny_namespaces()
    check_denial()
    if sys.argv[1:] != ['--probe-only']:
        from runner.cloud import main
        main()
