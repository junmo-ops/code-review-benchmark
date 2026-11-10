"""Preserve every local commit using one main branch and one history-preservation branch."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import subprocess
import sys

SAFE = re.compile(r'^[A-Za-z0-9_]+$')
PRESERVED = 'bench_preserved'
PARENT_LIMIT = 32


def git(repo, *args, input_text=None):
    executable = shutil.which('git')
    if not executable:
        raise ValueError('找不到 Git，请先安装并加入 PATH')
    # A caller's GIT_DIR / GIT_OBJECT_DIRECTORY must never redirect this operation.
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(LC_ALL='C', GIT_NO_LAZY_FETCH='1', GIT_OPTIONAL_LOCKS='0')
    if args[0] != 'push':
        env.update(GIT_ALLOW_PROTOCOL='', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    with tempfile.TemporaryDirectory(prefix='bench-empty-hooks-') as hooks:
        result = subprocess.run(
            [executable, '--no-replace-objects', '-c', 'core.hooksPath=' + hooks,
             '-c', 'core.fsmonitor=false', '-c', 'core.longpaths=true',
             '-C', str(repo), *args],
            input=input_text.encode('utf-8') if input_text is not None else None,
            capture_output=True, timeout=3600, env=env,
        )
    stderr = result.stderr.decode('utf-8', errors='replace')
    if result.returncode:
        reason = '请检查对象完整性、分支配置或权限'
        for marker, label in (
            ('extra input', 'Git 引用输入格式错误（检查 CRLF 换行或多余字符）'),
            ('not a symbolic ref', '缺少 origin/HEAD，请在配置中指定 main'),
            ('cannot lock ref', '引用锁冲突，请检查同名引用或残留锁文件'),
            ('missing', '存在缺失对象'),
            ('dubious ownership', 'Git 仓库所有权校验失败'),
            ('rejected', '远端拒绝推送，需查看平台分支/权限规则'),
        ):
            if marker in stderr:
                reason = label
                break
        # Do not print remote URLs or credentials from Git error output.
        if args[0] == 'update-ref':
            detail = re.sub(r'(?:https?|ssh|file)://[^\s]+', '[地址已隐藏]', stderr)
            detail = detail.replace('\r', r'\r').strip()[:1200]
            reason += '\nGit 详情：' + detail
        raise RuntimeError(f'Git {args[0]} 失败（{result.returncode}）：{reason}')
    return result.stdout.decode('utf-8', errors='replace').strip()


def ensure_full_repository(repo):
    if git(repo, 'rev-parse', '--is-shallow-repository') != 'false':
        raise ValueError('浅克隆不能确认完整历史，请先补全仓库')
    settings = git(repo, 'config', '--local', '--list').splitlines()
    for line in settings:
        key = line.split('=', 1)[0].lower()
        if key == 'extensions.partialclone' or (key.startswith('remote.') and key.endswith('.promisor')):
            raise ValueError('部分克隆含按需下载对象，不能保证离线完整；请先生成完整仓库副本')
    objects = Path(git(repo, 'rev-parse', '--git-path', 'objects'))
    objects = objects if objects.is_absolute() else repo / objects
    if any((objects / 'pack').glob('*.promisor')):
        raise ValueError('存在 promisor 对象包，不能保证离线完整')


def discover(root):
    found = {}
    for folder, dirs, files in os.walk(root):
        if '.git' in dirs or '.git' in files:
            path = Path(folder)
            found[path.relative_to(root).as_posix()] = path
            dirs[:] = []
        else:
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in {'node_modules', '__MACOSX'}]
    return found


def selected_repos(root, cases):
    repos = discover(root)
    if cases is None:
        return repos
    rows = [json.loads(line) for line in cases.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    result = {}
    for name in sorted({row['repo'] for row in rows}):
        owner, project = name.split('/', 1)
        candidates = [key for key in (name, owner, project, name.replace('/', '__')) if key in repos]
        if not candidates:
            raise ValueError('未找到案例仓库：' + name)
        result[name] = repos[candidates[0]]
    return result


def main_ref(repo, config):
    if config.get('main'):
        name = config['main']
    else:
        ref = git(repo, 'symbolic-ref', 'refs/remotes/origin/HEAD')
        prefix = 'refs/remotes/origin/'
        if not ref.startswith(prefix):
            raise ValueError('无法确定默认分支，请在配置中指定 main')
        name = ref[len(prefix):]
    if not SAFE.fullmatch(name):
        raise ValueError('主分支名称不符合规范，请先确认合法的目标分支名称')
    refs = git(repo, 'for-each-ref', '--format=%(refname)').splitlines()
    source = 'refs/heads/' + name
    if source not in refs:
        source = 'refs/remotes/origin/' + name
    return name, git(repo, 'rev-parse', '--verify', source + '^{commit}')


def make_plan(name, repo, config):
    ensure_full_repository(repo)
    if git(repo, 'for-each-ref', '--format=%(refname)', 'refs/replace'):
        raise ValueError('存在 replace 引用，请先确认原始历史')
    grafts = Path(git(repo, 'rev-parse', '--git-path', 'info/grafts'))
    grafts = grafts if grafts.is_absolute() else repo / grafts
    if grafts.exists():
        raise ValueError('存在 grafts，无法按原始历史迁移')
    main, main_sha = main_ref(repo, config)
    print('  枚举全部本地提交对象……', flush=True)
    objects = git(repo, 'cat-file', '--batch-all-objects', '--batch-check=%(objectname) %(objecttype)')
    commits = {line.split()[0] for line in objects.splitlines() if line.endswith(' commit')}
    if main_sha not in commits:
        raise ValueError('主分支对象缺失')
    # Traverse the original graph. Reject missing parents instead of silently dropping history.
    graph = git(repo, 'rev-list', '--parents', '--stdin', input_text='\n'.join(sorted(commits)) + '\n')
    traversed, parents = set(), set()
    for line in graph.splitlines():
        parts = line.split()
        traversed.add(parts[0])
        parents.update(parts[1:])
    if traversed != commits:
        raise ValueError('对象枚举与提交图不一致')
    main_history = set(git(repo, 'rev-list', main_sha).splitlines())
    tips = sorted(commits - parents - main_history)
    refs = [{'name': main, 'sha': main_sha}]
    if main == PRESERVED:
        raise ValueError('主分支与保留分支 bench_preserved 同名，请先调整配置')
    if tips:
        refs.append({'name': PRESERVED, 'sha': None})
    covered = set(git(repo, 'rev-list', '--stdin', input_text='\n'.join([main_sha, *tips]) + '\n').splitlines())
    if covered != commits:
        raise ValueError('规划分支没有覆盖全部提交，停止')
    return {'repository': name, 'source': str(repo.resolve()), 'main': main,
            'commit_count': len(commits), 'main_commit_count': len(main_history),
            'preserved_branches': int(bool(tips)), 'history_tips': tips,
            'synthetic_commits': [], 'refs': refs, 'commits': sorted(commits)}


def build_history_anchor(repo, plan):
    """Add parent links only; original commits and the main branch never change."""
    tips = plan['history_tips']
    if not tips:
        return
    tree = git(repo, 'rev-parse', plan['refs'][0]['sha'] + '^{tree}')
    level = tips
    while len(level) > 1:
        next_level = []
        for start in range(0, len(level), PARENT_LIMIT):
            parents = level[start:start + PARENT_LIMIT]
            if len(parents) == 1:
                next_level.append(parents[0])
                continue
            # Write deterministic, valid commit objects without shell quoting or
            # user/global identity settings. These are not code merge results.
            body = 'tree ' + tree + '\n'
            body += ''.join('parent ' + sha + '\n' for sha in parents)
            body += ('author Benchmark History <benchmark-history@example.invalid> 946684800 +0000\n'
                     'committer Benchmark History <benchmark-history@example.invalid> 946684800 +0000\n'
                     '\nPreserve benchmark history; not a code merge or evaluation target.\n')
            sha = git(repo, 'hash-object', '-t', 'commit', '-w', '--stdin', input_text=body)
            plan['synthetic_commits'].append(sha)
            next_level.append(sha)
        level = next_level
    plan['refs'][1]['sha'] = level[0]


def create_refs(repo, plan):
    build_history_anchor(repo, plan)
    current = {}
    for line in git(repo, 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads').splitlines():
        ref, sha = line.split()
        current[ref] = sha
    updates = []
    for row in plan['refs']:
        ref = 'refs/heads/' + row['name']
        if ref in current:
            if current[ref] != row['sha']:
                if row['name'] != PRESERVED or current[ref] not in set(git(repo, 'rev-list', row['sha']).splitlines()):
                    raise ValueError('同名分支不能快进，拒绝覆盖：' + row['name'])
                updates.append('update ' + ref + ' ' + row['sha'] + ' ' + current[ref])
        else:
            updates.append('create ' + ref + ' ' + row['sha'])
    if updates:
        git(repo, 'update-ref', '--stdin', input_text='\n'.join(updates) + '\n')


def push_refs(repo, plan, config):
    url = config.get('url', '')
    if not url or not url.startswith(('https://', 'http://', 'ssh://', 'git@')):
        raise ValueError('推送必须在配置中明确填写行内 url')
    if re.search(r'https?://[^/]*@', url):
        raise ValueError('请勿在 URL 中嵌入凭证，使用 Git 凭证管理器')
    # Batch to remain below Windows command-line length limits. No force/prune/mirror.
    refs = plan['refs']
    for i in range(0, len(refs), 40):
        specs = [r['sha'] + ':refs/heads/' + r['name'] for r in refs[i:i + 40]]
        git(repo, 'push', url, *specs)
        print(f'  已推送 {min(i + 40, len(refs))}/{len(refs)} 个引用', flush=True)


def verify(root, output, manifest=None):
    plans = json.loads((manifest or output / 'migration_manifest.json').read_text(encoding='utf-8'))
    repos = discover(root)
    failures = []
    for plan in plans:
        name = plan['repository']
        repo = repos.get(name)
        if repo is None:
            failures.append(name + '：未找到全新克隆仓库（目录须与清单的 repository 字段一致）')
            continue
        # Check reachable history, not leftover unreachable objects in an old clone.
        ensure_full_repository(repo)
        reachable = set(git(repo, 'rev-list', '--all').splitlines())
        missing = sorted(set(plan['commits']) - reachable)
        git(repo, 'fsck', '--full', '--no-reflogs')
        print(f'{name}: 缺失 {len(missing)}/{plan["commit_count"]} 个提交', flush=True)
        if missing:
            failures.append({'repository': name, 'missing': missing})
    output.mkdir(parents=True, exist_ok=True)
    (output / 'verification.json').write_text(json.dumps({'failures': failures}, ensure_ascii=False, indent=2), encoding='utf-8')
    if failures:
        raise ValueError('校验未通过，详见 verification.json')


def read_config(path):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path else {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--repos-root', type=Path)
    group.add_argument('--zip-dir', type=Path, help='批量处理目录中的 ZIP，输出新 ZIP，原包不动')
    parser.add_argument('--temp-dir', type=Path, help='ZIP 模式临时解压目录，需足够磁盘空间')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--cases', type=Path, help='可选 JSONL，仅筛选仓库；仍保留这些仓库全部已有提交')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--apply', action='store_true', help='创建保留分支，不改工作区')
    parser.add_argument('--push', action='store_true', help='配合 --apply 和 --config 明确推送到行内')
    parser.add_argument('--manifest', type=Path, help='--verify 使用的提交清单路径')
    parser.add_argument('--verify', action='store_true', help='在全新克隆目录验证清单，不创建分支或推送')
    args = parser.parse_args()
    if args.manifest and not args.verify:
        parser.error('--manifest 只用于 --verify')
    if args.zip_dir:
        if args.apply or args.push or args.verify or args.cases:
            parser.error('ZIP 模式自动处理临时副本，不与 --apply/--push/--verify/--cases 混用')
        from .archives import process_archives
        config = read_config(args.config)
        if args.temp_dir:
            args.temp_dir.mkdir(parents=True, exist_ok=True)
        process_archives(args.zip_dir, args.output, config, args.temp_dir)
        return
    if args.verify:
        if args.apply or args.push:
            parser.error('--verify 不可与 --apply/--push 同用')
        verify(args.repos_root, args.output, args.manifest)
        return
    if args.push and (not args.apply or not args.config):
        parser.error('--push 必须同时指定 --apply 和 --config')
    run_directory(args)


def run_directory(args):
    config = json.loads(args.config.read_text(encoding='utf-8-sig')) if args.config else {}
    repos = selected_repos(args.repos_root, args.cases)
    if not repos:
        raise ValueError('没有找到仓库，停止')
    plans = []
    # Finish and persist every plan before any repository mutation.
    for name, repo in sorted(repos.items()):
        print(name, flush=True)
        plan = make_plan(name, repo, config.get(name, {}))
        plans.append(plan)
        print(f'  {plan["commit_count"]} 个提交；主分支 {plan["main"]}；新增保留分支 {plan["preserved_branches"]}', flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'migration_manifest.json').write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.apply:
        for plan in plans:
            repo = Path(plan['source'])
            create_refs(repo, plan)
            (args.output / 'migration_manifest.json').write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding='utf-8')
            if args.push:
                push_refs(repo, plan, config.get(plan['repository'], {}))
    print('完成：' + ('已执行；仍需在全新行内克隆上 --verify' if args.apply else '仅预览，未修改仓库、未推送'))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print('失败：' + str(exc), file=sys.stderr)
        sys.exit(1)
