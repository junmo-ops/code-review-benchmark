"""Offline AACR selection from local Git histories; Python standard library only."""
import argparse
from collections import defaultdict
import csv
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


class GitFailure(RuntimeError):
    pass


BUNDLED_DATASET = Path(__file__).resolve().with_name('aacr-official.jsonl')
PROJECT_DATASET = Path(__file__).resolve().parents[1] / 'data/sources/aacr/positive_samples.standard.jsonl'
DEFAULT_DATASET = BUNDLED_DATASET if BUNDLED_DATASET.is_file() else PROJECT_DATASET


def git(repo, *args, input_data=None):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_NO_LAZY_FETCH='1', GIT_OPTIONAL_LOCKS='0')
    command = ['git', '-c', 'safe.directory=' + str(repo), '-c', 'core.fsmonitor=false',
               '-c', 'protocol.allow=never',
               '-C', str(repo), *args]
    try:
        return subprocess.run(command, input=input_data, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=90, env=env)
    except subprocess.TimeoutExpired:
        raise GitFailure('Git 检查超过 90 秒') from None
    except OSError:
        raise GitFailure('无法执行 Git 或读取仓库') from None


def discover(roots, warnings):
    found = set()
    skipped = {'.git', 'node_modules', '.venv', '__pycache__'}
    for root in roots:
        def walk_error(error):
            warnings.append('无法扫描目录：' + str(error.filename))
        for folder, dirs, files in os.walk(root, onerror=walk_error, followlinks=False):
            path = Path(folder).resolve()
            worktree = '.git' in dirs or '.git' in files
            bare = 'HEAD' in files and 'config' in files and 'objects' in dirs
            if worktree or bare:
                found.add(path)
                dirs[:] = []
            else:
                dirs[:] = [name for name in dirs if name not in skipped]
    return sorted(found, key=str)


def matching_names(path, known):
    remote = git(path, 'config', '--get', 'remote.origin.url').stdout.decode('utf-8', 'replace').strip()
    match = re.search(r'github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?/?$', remote, re.I)
    if match:
        # An explicit origin is stronger evidence than a folder alias.
        name = (match[1] + '/' + match[2]).lower()
        return [name] if name in known else []
    name = path.name.removesuffix('.git').lower()
    candidates = {name.replace('__', '/'), path.parent.name.lower() + '/' + name}
    exact = sorted(candidates.intersection(known))
    if exact:
        return exact
    suffixes = [repo for repo in known if repo.rsplit('/', 1)[1] == name]
    return suffixes if len(suffixes) == 1 else []


def index_repos(paths, known, warnings):
    indexed = defaultdict(list)
    for path in paths:
        try:
            for name in matching_names(path, known):
                indexed[name].append(path)
        except GitFailure as error:
            warnings.append(str(path) + '：' + str(error))
    return indexed


@lru_cache(maxsize=None)
def verify_tree(repo, commit):
    listing = git(repo, 'ls-tree', '-r', '-z', commit)
    if listing.returncode:
        return False
    objects = set()
    for entry in listing.stdout.split(b'\0'):
        if not entry:
            continue
        fields = entry.split(b'\t', 1)[0].split()
        if len(fields) == 3 and fields[1] == b'blob':
            objects.add(fields[2])
    if not objects:
        return True
    result = git(repo, 'cat-file', '--batch-check=%(objectname) %(objecttype)',
                 input_data=b'\n'.join(sorted(objects)) + b'\n')
    lines = result.stdout.splitlines()
    return (result.returncode == 0 and len(lines) == len(objects)
            and all(line.endswith(b' blob') for line in lines))


def inspect_case(path, case):
    for field in ('base_commit', 'head_commit'):
        commit = case[field]
        result = git(path, 'cat-file', '-t', commit)
        if result.returncode or result.stdout.strip() != b'commit':
            return '缺少提交', field + ' 不存在'
        if not verify_tree(path, commit):
            return '源码不完整', field + ' 对应目录树或文件对象缺失'
    diff = git(path, 'diff', '--no-ext-diff', '--no-textconv', '--name-only',
               case['base_commit'], case['head_commit'])
    if diff.returncode:
        return '差异不可读', '无法读取 base 到 head 的差异'
    if not diff.stdout.strip():
        return '无代码差异', 'base 到 head 没有文件变化'
    return '可用', ''


def check_case(case, paths):
    row = {key: case[key] for key in ('instance_id', 'repo', 'base_commit', 'head_commit')}
    row.update(status='未找到仓库', reason='目录或 origin 未匹配到官方仓库', local_path='')
    for path in paths:
        try:
            status, reason = inspect_case(path, case)
        except GitFailure as error:
            status, reason = '检查失败', str(error)
        row.update(status=status, reason=reason, local_path=str(path))
        if status == '可用':
            break
    return row


def select_balanced(cases, limit):
    groups = defaultdict(list)
    for case in sorted(cases, key=lambda item: item['instance_id']):
        groups[case['repo']].append(case)
    selected = []
    while len(selected) < limit and any(groups.values()):
        for repo in sorted(groups):
            if groups[repo] and len(selected) < limit:
                selected.append(groups[repo].pop(0))
    return selected


def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')


def save_outputs(output, dataset, rows, cases, paths, warnings, limit):
    output.mkdir(parents=True, exist_ok=True)
    usable = {row['instance_id'] for row in rows if row['status'] == '可用'}
    available = [case for case in cases if case['instance_id'] in usable]
    selected = select_balanced(available, limit)
    write_jsonl(output / 'available.dataset.jsonl', available)
    write_jsonl(output / 'selected.dataset.jsonl', selected)
    report = dict(requested=limit, available=len(available), selected=len(selected),
                  enough=len(selected) == limit, discovered_repositories=len(paths),
                  discovered_paths=[str(path) for path in paths],
                  dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
                  selection='按仓库轮流选择，仓库和案例编号排序，确定性抽取',
                  selected_instance_ids=[case['instance_id'] for case in selected],
                  warnings=warnings, cases=rows)
    (output / 'check-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with (output / 'check-result.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print('可用案例：' + str(len(available)) + '；已选择：' + str(len(selected)))
    if len(selected) < limit:
        print('距离目标仍差 ' + str(limit - len(selected)) + ' 例，具体原因见 check-result.json。')
    print('结果目录：' + str(output.resolve()))
    print('请回传 check-result.json。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repos-root', type=Path, nargs='+', required=True, help='解压目录；可填写多个目录')
    parser.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--output', type=Path, default=Path('aacr-selection-result'))
    args = parser.parse_args()
    if not shutil.which('git'):
        parser.error('未找到 Git，请先确保 git --version 可以运行')
    if args.limit < 1 or any(not root.is_dir() for root in args.repos_root):
        parser.error('目标数量必须大于零，仓库目录必须存在')
    if not args.dataset.is_file():
        parser.error('未找到官方清单，请将工具 ZIP 完整解压后运行')
    cases = [json.loads(line) for line in args.dataset.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    if not cases:
        parser.error('官方清单为空')
    warnings = []
    print('正在寻找本地 Git 仓库……', flush=True)
    paths = discover(args.repos_root, warnings)
    indexed = index_repos(paths, {case['repo'].lower() for case in cases}, warnings)
    print('发现 ' + str(len(paths)) + ' 个仓库；匹配官方仓库 ' + str(len(indexed)) + ' 个', flush=True)
    rows = []
    for number, case in enumerate(cases, 1):
        row = check_case(case, indexed.get(case['repo'].lower(), []))
        rows.append(row)
        print(str(number) + '/' + str(len(cases)) + ' ' + case['instance_id'] + '：' + row['status'], flush=True)
    save_outputs(args.output, args.dataset, rows, cases, paths, warnings, args.limit)


if __name__ == '__main__':
    main()
