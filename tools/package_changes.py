"""Package uncommitted sources after the mandatory release gate."""
import argparse
from datetime import datetime
import hashlib
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def git_names(*args):
    raw = subprocess.check_output(['git', *args, '-z'], cwd=ROOT)
    return [name for name in raw.decode('utf-8').split('\0') if name]


def collect_changes():
    changed = git_names('diff', 'HEAD', '--name-only', '--diff-filter=ACMRT')
    added = git_names('ls-files', '--others', '--exclude-standard')
    deleted = git_names('diff', 'HEAD', '--name-only', '--diff-filter=D')
    return sorted(set(changed + added)), deleted


def build_archive(target, names, deleted):
    staging = target.with_suffix('.zip.part')
    digests = {}
    try:
        with zipfile.ZipFile(staging, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                path = ROOT / name
                if path.is_symlink() or not path.is_file():
                    raise ValueError('不能打包链接或非普通文件：' + name)
                data = path.read_bytes()
                digests[name] = hashlib.sha256(data).hexdigest()
                info = zipfile.ZipInfo.from_file(path, arcname=name)
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
            if deleted:
                archive.writestr('DELETE_FILES.txt', '\n'.join(deleted) + '\n')
        verify_archive(staging, digests)
        staging.replace(target)
    finally:
        staging.unlink(missing_ok=True)


def verify_archive(path, digests):
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP 完整性校验失败')
        for name, expected in digests.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError('ZIP 内容校验失败：' + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    target = args.output or ROOT.parent / ('未提交改动-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.zip')
    target = target.resolve()
    if target.is_relative_to(ROOT):
        raise ValueError('ZIP 请放在项目目录外，避免后续把交付包再次打入源码')
    subprocess.run(['node', str(ROOT / 'scripts/project.mjs'), 'verify'], cwd=ROOT, check=True)
    names, deleted = collect_changes()
    if not names and not deleted:
        raise ValueError('没有未提交改动')
    target.parent.mkdir(parents=True, exist_ok=True)
    build_archive(target, names, deleted)
    print(f'交付包：{target}；源码文件 {len(names)} 个；删除项 {len(deleted)} 个。')


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        print('交付校验失败，未生成新 ZIP。', file=sys.stderr)
        raise SystemExit(error.returncode) from None
