"""Build an independent repository-resource ZIP for the benchmark workbench."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit
import zipfile
import sys

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "backend/src"))
sys.path.insert(0, str(ROOT))
from runner.git_bundles import create_complete_bundle


def repository_name(path):
    result = subprocess.run(['git', '-C', str(path), 'remote', 'get-url', 'origin'],
                            capture_output=True, text=True)
    if result.returncode:
        raise ValueError('仓库没有 origin，请为其设置远程地址：' + str(path))
    value = result.stdout.strip().removesuffix('.git')
    match = re.search(r'(?:^|[:/])([^/:]+)/([^/:]+)$', value)
    if not match:
        raise ValueError('无法从 origin 识别 owner/repo：' + str(path))
    return match.group(1) + '/' + match.group(2)


def discover(root):
    values = []
    for folder, dirs, files in os.walk(root):
        if '.git' in dirs or '.git' in files:
            values.append(Path(folder))
            dirs[:] = []
            continue
        dirs[:] = [name for name in dirs if name not in {'node_modules', '.venv', '__pycache__'}]
    return values


def add_repository(path, name, archive, records, repos):
    path = Path(path).resolve()
    if not path.is_dir() or subprocess.run(['git', '-C', str(path), 'rev-parse', '--git-dir'],
                                           capture_output=True).returncode:
        raise ValueError('不是可用的 Git 仓库：' + str(path))
    slug = name.replace('/', '__')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', slug) or slug in repos:
        raise ValueError('仓库名称无效或重复：' + name)
    with tempfile.TemporaryDirectory(prefix='repo-bundle-') as folder:
        bundle = Path(folder) / (slug + '.bundle')
        create_complete_bundle(path, bundle)
        digest = hashlib.sha256()
        with bundle.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        key = 'bundles/' + slug + '.bundle'
        archive.write(bundle, key, compress_type=zipfile.ZIP_STORED)
        records[key] = {'size': bundle.stat().st_size, 'sha256': digest.hexdigest()}
        repos[slug] = {'name': name}
        print(name + ': ' + format(bundle.stat().st_size / 1024 / 1024, '.1f') + ' MiB', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repos-root', type=Path, action='append', default=[], help='含一个或多个 Git 仓库的目录，可重复指定')
    parser.add_argument('--repo', action='append', default=[], metavar='OWNER/REPO=PATH', help='显式指定仓库名称和本地目录，可重复指定')
    parser.add_argument('--output', type=Path, default=Path('repository-resources.zip'))
    args = parser.parse_args()
    inputs = [(path, repository_name(path)) for root in args.repos_root for path in discover(root)]
    for item in args.repo:
        name, separator, path = item.partition('=')
        if not separator or not name or not path:
            parser.error('--repo 格式应为 OWNER/REPO=PATH')
        inputs.append((Path(path), name))
    if not inputs:
        parser.error('请至少指定 --repos-root 或 --repo')
    temporary = args.output.with_name(args.output.name + '.part')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    records, repos = {}, {}
    try:
        with zipfile.ZipFile(temporary, 'w', allowZip64=True) as archive:
            for path, name in inputs:
                add_repository(path, name, archive, records, repos)
            manifest = {'format': 'code-review-benchmark-repositories-v1',
                        'repositories': repos, 'files': records}
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(args.output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    print('仓库资源包：' + str(args.output.resolve()))
    print('仓库数量：' + str(len(repos)))


if __name__ == '__main__':
    main()
