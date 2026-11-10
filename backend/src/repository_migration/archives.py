"""Sequential ZIP preparation; originals stay untouched, no network operations."""
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import zipfile


def validate_zip_component(part):
    reserved = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}
    stem = part.split('.', 1)[0].upper()
    if (part.endswith((' ', '.')) or any(ord(char) < 32 for char in part)
            or any(char in '<>:"|?*' for char in part)
            or stem in reserved or re.fullmatch(r'(COM|LPT)[1-9¹²³]', stem)):
        raise ValueError('ZIP 包含 Windows 不支持的名称：' + part)


def check_spellings(path, spellings, name):
    for length in range(1, len(path.parts) + 1):
        spelling = '/'.join(path.parts[:length])
        folded = spelling.casefold()
        if folded in spellings and spellings[folded] != spelling:
            raise ValueError('ZIP 路径存在大小写冲突，不能跨 Windows 安全处理：' + name)
        spellings[folded] = spelling


def unsafe_path(name, path):
    return '\\' in name or path.is_absolute() or '..' in path.parts or any(':' in p for p in path.parts)


def checked_entries(archive):
    entries, seen, spellings = [], set(), {}
    for info in archive.infolist():
        name = info.filename
        path = PurePosixPath(name)
        if unsafe_path(name, path):
            raise ValueError('ZIP 含不安全路径：' + name)
        if not path.parts:
            continue
        for part in path.parts:
            validate_zip_component(part)
        check_spellings(path, spellings, name)
        key = path.as_posix().casefold()
        if key in seen:
            raise ValueError('ZIP 存在重复或仅大小写不同的路径，不能跨 Windows 安全解压：' + name)
        seen.add(key)
        entries.append((info, path.as_posix()))
    files = {name.casefold() for info, name in entries if not info.is_dir()}
    for _, name in entries:
        if any(p.as_posix().casefold() in files for p in PurePosixPath(name).parents if p.parts):
            raise ValueError('ZIP 存在文件/目录冲突：' + name)
    return entries


def extract(archive_path, root):
    metadata = {}
    with zipfile.ZipFile(archive_path) as archive:
        entries = checked_entries(archive)
        required = sum(info.file_size for info, _ in entries)
        maximum = int(os.environ.get('BENCH_ZIP_UNPACK_MAX_MIB', '8192')) * 1024 * 1024
        if len(entries) > 500000 or required > maximum:
            raise ValueError('ZIP 解压内容超过工具限制（默认 8 GiB、50 万条目）')
        if shutil.disk_usage(root).free < required + 256 * 1024 * 1024:
            raise ValueError('临时目录磁盘空间不足')
        for info, name in entries:
            dest = root / name
            metadata[name] = info
            if info.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Materialize symlink text as a regular file; never follow ZIP links.
            # Its original symlink attributes are restored in the output ZIP.
            with archive.open(info) as source, dest.open('wb') as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
    return metadata


def validate_repo(repo, root, metadata, git):
    gitdir = repo / '.git'
    if not gitdir.is_dir():
        raise ValueError('ZIP 内必须包含独立 .git 目录，不支持外部 worktree 指针')
    prefix = gitdir.relative_to(root).as_posix() + '/'
    for name, info in metadata.items():
        if name.startswith(prefix) and stat.S_ISLNK(info.external_attr >> 16):
            raise ValueError('.git 内含符号链接，不能确保离线完整性')
    for relative in ('objects/info/alternates', 'objects/info/http-alternates'):
        path = gitdir / relative
        if path.exists() and path.read_bytes().strip():
            raise ValueError('仓库依赖外部对象目录，先在原始仓库生成独立副本')
    if (gitdir / 'commondir').exists():
        raise ValueError('仓库依赖外部 common dir')
    from .history import ensure_full_repository
    ensure_full_repository(repo)
    git(repo, 'fsck', '--full', '--no-reflogs')


def pack(root, output, metadata):
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
        for folder, dirs, files in os.walk(root):
            for name in sorted(dirs + files):
                path = Path(folder) / name
                relative = path.relative_to(root).as_posix()
                old = metadata.get(relative)
                if path.is_dir():
                    info = zipfile.ZipInfo(relative + '/')
                    info.external_attr = (stat.S_IFDIR | 0o755) << 16
                    archive.writestr(info, b'')
                    continue
                info = zipfile.ZipInfo(relative, old.date_time if old else (2020, 1, 1, 0, 0, 0))
                info.create_system = old.create_system if old else 3
                info.external_attr = old.external_attr if old else (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                with path.open('rb') as source, archive.open(info, 'w', force_zip64=True) as target:
                    shutil.copyfileobj(source, target, 1024 * 1024)
    with zipfile.ZipFile(output) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError('输出 ZIP CRC 校验失败：' + bad)


def repository_path(source):
    """Find the actual repository root, regardless of ZIP wrapper depth."""
    roots = set()
    with zipfile.ZipFile(source) as archive:
        for _, name in checked_entries(archive):
            parts = PurePosixPath(name).parts
            if '.git' in parts:
                roots.add(parts[:parts.index('.git')])
    if len(roots) != 1:
        raise ValueError('ZIP 必须包含唯一仓库，无法确定输出项目名称')
    root = next(iter(roots))
    if not root:
        raise ValueError('仓库直接位于 ZIP 根目录，缺少项目目录名；请用项目目录重新打包')
    return '/'.join(root)


def archive_project_name(source):
    return PurePosixPath(repository_path(source)).name + '.zip'


def require_available(output, filename):
    if any(path.name.casefold() == filename.casefold() for path in output.iterdir()):
        raise ValueError('输出名称已存在或大小写冲突，不覆盖：' + filename)


def retain_heads(repo, plan, git):
    keep = {'refs/heads/' + row['name'] for row in plan['refs']}
    heads = git(repo, 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads').splitlines()
    deletes = ['delete ' + line for line in heads if line.split()[0] not in keep]
    if deletes:
        git(repo, 'update-ref', '--stdin', input_text='\n'.join(deletes) + '\n')
    git(repo, 'symbolic-ref', 'HEAD', 'refs/heads/' + plan['main'])
    actual = set(git(repo, 'for-each-ref', '--format=%(refname)', 'refs/heads').splitlines())
    if actual != keep or len(actual) > 2:
        raise ValueError('输出分支数量校验失败')


def prepare_staged(source, destination, config, temp_root, output_name, progress=print):
    from .history import discover, make_plan, create_refs, git
    destination.mkdir(parents=True, exist_ok=False)
    try:
        with tempfile.TemporaryDirectory(prefix='bench-zip-', dir=temp_root) as temp:
            root = Path(temp)
            progress('正在解压并校验 ZIP')
            metadata = extract(source, root)
            repos = discover(root)
            if not repos:
                raise ValueError('ZIP 内未找到包含 .git 历史的仓库')
            plans = []
            for name, repo in sorted(repos.items()):
                progress('正在检查仓库：' + name)
                validate_repo(repo, root, metadata, git)
                progress('正在枚举提交并整理历史')
                plan = make_plan(name, repo, config.get(name, {}))
                create_refs(repo, plan)
                progress('正在校验提交完整性')
                covered = set(git(repo, 'rev-list', '--stdin', input_text='\n'.join('refs/heads/' + r['name'] for r in plan['refs']) + '\n').splitlines())
                if covered != set(plan['commits']) | set(plan['synthetic_commits']):
                    raise ValueError('创建分支后提交覆盖校验失败')
                # Only the disposable ZIP copy loses surplus local branches.
                retain_heads(repo, plan, git)
                git(repo, 'fsck', '--full', '--no-reflogs')
                plan['source'] = name
                plans.append(plan)
            (destination / 'migration_manifest.json').write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding='utf-8')
            instructions = ['先解压 ' + output_name + '；下列 URL 占位符必须换成对应的行内仓库地址。',
                            '仅推送主分支和测评保留分支；不要用 --mirror。已有远端分支不会自动删除。', '']
            for plan in plans:
                instructions.append('进入仓库目录：' + plan['repository'])
                instructions.append('git push "行内仓库URL" refs/heads/' + plan['main'] + ':refs/heads/' + plan['main'])
                if plan['preserved_branches']:
                    instructions.append('git push "行内仓库URL" refs/heads/bench_preserved:refs/heads/bench_preserved')
                instructions.append('')
            (destination / '推送说明.txt').write_text('\n'.join(instructions), encoding='utf-8')
            partial = destination / (output_name + '.part')
            progress('正在压缩并校验输出 ZIP')
            pack(root, partial, metadata)
            partial.replace(destination / output_name)
            return {'file': source.name, 'output_file': output_name, 'status': 'success', 'repositories': len(plans),
                    'commits': sum(p['commit_count'] for p in plans),
                    'preserved_branches': sum(p['preserved_branches'] for p in plans)}
    except BaseException:
        # Preserve the original failure even if Windows still holds a file handle.
        shutil.rmtree(destination, ignore_errors=True)
        raise


def prepare_one(source, output, records, config, temp_root):
    output_name = archive_project_name(source)
    require_available(output, output_name)
    target = output / output_name
    print('  输出包：' + output_name, flush=True)
    # Stage on the output filesystem so final publication is an atomic rename.
    with tempfile.TemporaryDirectory(prefix='packing-', dir=records) as stage:
        work = Path(stage) / 'work'
        result = prepare_staged(source, work, config, temp_root, output_name)
        require_available(output, output_name)
        (work / 'migration_manifest.json').replace(records / (output_name + '.manifest.json'))
        (work / '推送说明.txt').replace(records / (output_name + '.push.txt'))
        (work / output_name).replace(target)
        return result


def process_archives(source_dir, output, config, temp_root=None):
    source_dir, output = source_dir.resolve(), output.resolve()
    if output == source_dir:
        raise ValueError('输出目录不能与输入目录相同')
    sources = sorted(p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() == '.zip')
    if not sources:
        raise ValueError('输入目录没有 ZIP 文件')
    output.mkdir(parents=True, exist_ok=True)
    records = output.with_name(output.name + '_records')
    if records == source_dir:
        raise ValueError('辅助记录目录不能与源目录相同，请换一个输出目录')
    records.mkdir(parents=True, exist_ok=True)
    print('校验清单和日志：' + str(records), flush=True)
    rows = []
    for i, source in enumerate(sources, 1):
        print(f'[{i}/{len(sources)}] {source.name}', flush=True)
        try:
            row = prepare_one(source, output, records, config, temp_root)
        except Exception as exc:
            row = {'file': source.name, 'status': 'failed', 'error': str(exc)}
            print('  失败：' + str(exc), flush=True)
        rows.append(row)
        pending = records / 'batch_report.json.part'
        pending.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
        pending.replace(records / 'batch_report.json')
    failures = sum(row['status'] != 'success' for row in rows)
    print(f'完成：成功 {len(rows) - failures}，失败 {failures}；未推送任何远端。', flush=True)
    if failures:
        raise ValueError('部分 ZIP 失败，详见 ' + str(records / 'batch_report.json') + '；已成功的输出保留')
