"""Audit case commits and export one supplement for unreferenced case histories.

Use the unchanged repositories extracted from the original uploaded ZIPs.
Their existing ref histories are prerequisites, checked by the server on import.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/src'))
sys.path.insert(0, str(ROOT))
from runner.git_bundles import git
from tools.build_repository_upload import discover, repository_name


def read_cases(path):
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist()
                     if Path(name).name == 'dataset.jsonl']
            if len(names) != 1:
                raise ValueError('案例 ZIP 必须包含唯一的 dataset.jsonl')
            text = archive.read(names[0]).decode('utf-8-sig')
    else:
        text = path.read_text(encoding='utf-8-sig')
    cases = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not cases:
        raise ValueError('案例清单为空')
    return cases


def group_commits(cases):
    groups = {}
    for case in cases:
        name = case['repo']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', name):
            raise ValueError('无效仓库名称：' + name)
        commits = groups.setdefault(name, set())
        for key in ('base_commit', 'head_commit'):
            oid = case[key]
            if not re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})', oid):
                raise ValueError('无效提交：' + str(oid))
            commits.add(oid)
    return groups


def source_repositories(root, names):
    sources = {}
    for path in discover(root):
        name = repository_name(path)
        if name not in names:
            continue
        if name in sources:
            raise ValueError('发现同名仓库，请移除重复副本：' + name)
        sources[name] = path
    return sources


def inspect_repository(name, path, commits):
    result = {'repo': name, 'path': str(path) if path else None,
              'required_commits': sorted(commits), 'unreferenced': [], 'missing': []}
    if path is None:
        result['missing'] = sorted(commits)
        return result
    for oid in sorted(commits):
        try:
            git(path, 'cat-file', '-e', oid + '^{commit}')
        except ValueError:
            result['missing'].append(oid)
            continue
        if not git(path, 'for-each-ref', '--contains=' + oid, '--format=%(refname)').strip():
            result['unreferenced'].append(oid)
    return result


def export_supplement(path, commits, target, folder):
    repo = folder / 'source.git'
    repo.mkdir()
    git(repo, 'init', '--bare', '--quiet')
    objects = git(path, 'rev-parse', '--git-path', 'objects').decode().strip()
    objects = (path.resolve() / objects).resolve()
    (repo / 'objects/info/alternates').write_bytes((objects.as_posix() + '\n').encode('utf-8'))
    existing = git(path, 'for-each-ref', '--format=%(objectname)').decode().splitlines()
    updates = ['update refs/bench-original/' + str(index) + ' ' + oid
               for index, oid in enumerate(sorted(set(existing)))]
    # Flat ref names also work with servers that imported the old bench-repair leaf.
    updates.extend('update refs/heads/bench-supplement-' + oid + ' ' + oid for oid in commits)
    git(repo, 'update-ref', '--stdin', input=('\n'.join(updates) + '\n').encode())
    git(repo, 'bundle', 'create', str(target), '--glob=refs/heads/bench-supplement-*',
        '--not', '--glob=refs/bench-original/*')


def add_supplement(archive, entry, manifest):
    name = entry['repo']
    slug = name.replace('/', '__')
    with tempfile.TemporaryDirectory(prefix='batch-repair-') as folder:
        folder = Path(folder)
        target = folder / (slug + '.bundle')
        export_supplement(Path(entry['path']), entry['required_commits'], target, folder)
        digest = hashlib.sha256()
        with target.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        member = 'bundles/' + slug + '.bundle'
        manifest['repositories'][slug] = {'name': name,
                                           'required_commits': entry['required_commits']}
        manifest['files'][member] = {'size': target.stat().st_size, 'sha256': digest.hexdigest()}
        archive.write(target, member, compress_type=zipfile.ZIP_STORED)
        print(f'{name}：补充 {len(entry["unreferenced"])} 个无引用提交，'
              f'{target.stat().st_size / 1024**2:.2f} MiB', flush=True)


def write_archive(output, entries):
    manifest = {'format': 'code-review-benchmark-repositories-v1', 'repair': True,
                'repositories': {}, 'files': {}}
    temporary = output.with_name(output.name + '.part')
    try:
        with zipfile.ZipFile(temporary, 'w') as archive:
            for entry in entries:
                add_supplement(archive, entry, manifest)
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True, help='实际使用的案例配置 ZIP 或 JSONL')
    parser.add_argument('--repos-root', type=Path, required=True, help='原上传 ZIP 解压所得仓库的根目录')
    parser.add_argument('--output', type=Path, default=Path('case-repositories-repair.zip'))
    args = parser.parse_args()
    cases = read_cases(args.cases)
    groups = group_commits(cases)
    sources = source_repositories(args.repos_root, groups)
    entries = []
    for index, (name, commits) in enumerate(sorted(groups.items()), 1):
        print(f'检查仓库 {index}/{len(groups)}：{name}', flush=True)
        entries.append(inspect_repository(name, sources.get(name), commits))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = args.output.with_suffix('.report.json')
    report.write_text(json.dumps({'case_count': len(cases), 'repositories': entries},
                                 ensure_ascii=False, indent=2), encoding='utf-8')
    if any(entry['missing'] for entry in entries):
        raise SystemExit('原仓库缺失或不含所需提交；未生成补充包。详情：' + str(report.resolve()))
    repairs = [entry for entry in entries if entry['unreferenced']]
    if not repairs:
        raise SystemExit('全部案例提交均有引用，无需此类补充。请检查服务器仓库版本。报告：' + str(report.resolve()))
    write_archive(args.output, repairs)
    print('批量补充包：' + str(args.output.resolve()))
    print('检查报告：' + str(report.resolve()))
    print('在仓库管理页上传一次；合并成功后重新导入案例配置。')


if __name__ == '__main__':
    main()
