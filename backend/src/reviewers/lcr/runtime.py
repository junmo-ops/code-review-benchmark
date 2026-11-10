"""Inspect the packaged local runtime without a preparation marker."""
import hashlib
import platform
import shutil
import subprocess


def local_paths(root):
    windows = platform.system() == 'Windows'
    validate_platform(windows)
    package = root/'.runtime/lcr/node_modules/@LV01.30/leancode-code-review-cli' if windows else root/'lcr'
    binary = package/'dist'/('lcr-win32-x64.exe' if windows else 'lcr-darwin-arm64')
    paths = [binary, root/'bin'/('lcr.cmd' if windows else 'lcr'), package/'package.json']
    if windows:
        paths += [package/'bin/lcr.js', package/'bin/version-check.js',
                  root/'backend/src/reviewers/lcr/windows-runtime.json']
    missing = [str(path.relative_to(root)) for path in paths if not path.is_file()]
    if missing:
        raise ValueError('项目 LCR 运行文件缺失：' + '、'.join(missing) +
                         '；Windows 请执行 npm install --prefix .runtime/lcr @LV01.30/leancode-code-review-cli@2.0.1')
    if not shutil.which('git') or (windows and not shutil.which('node')):
        raise ValueError('LCR 运行需要 PATH 中的 Git，Windows 还需要 Node.js')
    rules = sorted(path for path in (package/'rules').rglob('*') if path.is_file())
    if not rules:
        raise ValueError('LCR rules 目录缺失或为空，请重新准备项目 LCR 包')
    return binary, paths + rules


def local_identity(root):
    binary, paths = local_paths(root)
    try:
        result = subprocess.run([str(binary), 'version'], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError('LCR 无法启动，请检查二进制平台和执行权限') from error
    if result.returncode:
        raise ValueError('LCR version 执行失败，请检查运行依赖')
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


def validate_platform(windows):
    machine = platform.machine().lower()
    if (windows and machine not in {'amd64', 'x86_64'}) or (not windows and machine != 'arm64'):
        raise ValueError('项目本地 LCR 仅支持 Windows x64 或 macOS arm64')
