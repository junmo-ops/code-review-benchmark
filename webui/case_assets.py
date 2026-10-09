"""Validate and register repeatable benchmark case uploads without model calls."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile

UPLOAD_FORMAT = 'code-review-benchmark-cases-v1'
MAX_FILES = 10002
from .case_upload import limits


def _registry_path(data):
    return Path(data) / 'case-datasets' / 'registry.json'


def _registry(data):
    try:
        value = json.loads(_registry_path(data).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) and isinstance(value.get('datasets'), list) else {'datasets': []}
    except (OSError, ValueError):
        return {'datasets': []}


def registered_datasets(data):
    base = (Path(data) / 'case-datasets').resolve()
    result = []
    for row in _registry(data)['datasets']:
        if not isinstance(row, dict) or not isinstance(row.get('file'), str):
            continue
        try:
            original = Path(data) / row['file']
            path = original.resolve()
            repositories = row.get('repositories')
            if (path.is_relative_to(base) and path.is_file() and not original.is_symlink()
                    and isinstance(repositories, list) and repositories
                    and all(isinstance(slug, str) and re.fullmatch(r'[A-Za-z0-9_.-]+', slug)
                            and (Path(data) / 'repos' / slug / '.git').is_dir()
                            for slug in repositories)):
                result.append(row)
        except (OSError, ValueError):
            continue
    return result


def _legacy_ready(root, data):
    manifest_path = Path(root) / 'deployment/aacr-cases-manifest.json'
    try:
        marker = json.loads((Path(data) / 'case-assets.json').read_text(encoding='utf-8'))
        return (marker['manifest_sha256'] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                and bool(marker['repositories'])
                and all((Path(data) / 'repos' / slug / '.git').is_dir() for slug in marker['repositories']))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def status(root, data):
    datasets = registered_datasets(data)
    legacy = _legacy_ready(root, data)
    ready = bool(datasets) or legacy
    names = [item.get('name', item.get('id', '案例集')) for item in datasets]
    message = f'已导入 {len(names)} 份案例集，可继续上传更多。' if names else (
        '已有案例已准备；可以继续导入其他案例集。' if legacy else
        '请上传案例 ZIP。导入和准备案例不会调用模型。')
    return dict(enabled=True, ready=ready, name='案例集导入', message=message, **limits(),
                datasets=[{'name': item.get('name'), 'suite': item.get('suite'), 'count': item.get('count')}
                          for item in datasets])


def _safe_member(name):
    path = PurePosixPath(name)
    return (name and not path.is_absolute() and '\\' not in name
            and all(part not in ('', '.', '..') for part in path.parts)
            and (name == 'dataset.jsonl' or (len(path.parts) == 2 and path.parts[0] == 'bundles'
                                                and path.suffix == '.bundle')))


def _extract_custom(archive, folder):
    config = limits()
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        names = [entry.filename for entry in entries]
        manifest, suite_id, expected = _validate_archive_manifest(source, entries, names)
        _extract_archive_files(source, entries, expected, folder, config)
    cases, dataset, slugs = _validate_uploaded_dataset(folder, suite_id, expected)
    return manifest, suite_id, cases, dataset, slugs


def _validate_archive_manifest(source, entries, names):
    if len(entries) > MAX_FILES or len(names) != len(set(names)) or 'manifest.json' not in names:
        raise ValueError('案例 ZIP 缺少清单、含重复文件或文件过多')
    info = next(entry for entry in entries if entry.filename == 'manifest.json')
    if info.is_dir() or info.file_size > 8 * 1024 * 1024:
        raise ValueError('案例 ZIP 的 manifest.json 无效或过大')
    try:
        manifest = json.loads(source.read(info).decode('utf-8-sig'))
    except (UnicodeDecodeError, ValueError, KeyError):
        raise ValueError('案例 ZIP 的 manifest.json 无效') from None
    if (not isinstance(manifest, dict) or manifest.get('format') != UPLOAD_FORMAT
            or not isinstance(manifest.get('name'), str) or not manifest['name'].strip()
            or len(manifest['name']) > 100):
        raise ValueError('案例 ZIP 格式或案例集名称无效')
    suite_id = manifest.get('suite')
    if not isinstance(suite_id, str) or suite_id not in {'aacr', 'swe_prbench_llm', 'codefuse_cr'}:
        raise ValueError('网页上传当前支持 AACR、SWE-PRBench 和 CodeFuse 案例集')
    from suites.registry import get_suite
    get_suite(suite_id)
    expected = manifest.get('files')
    if not isinstance(expected, dict) or set(expected) != set(names) - {'manifest.json'}:
        raise ValueError('案例 ZIP 文件与 manifest.json 清单不匹配')
    if 'dataset.jsonl' not in expected:
        raise ValueError('案例 ZIP 必须包含 dataset.jsonl')
    return manifest, suite_id, expected


def _extract_archive_files(source, entries, expected, folder, config):
    total = 0
    for entry in entries:
        if entry.filename == 'manifest.json':
            continue
        record = expected[entry.filename]
        mode = stat.S_IFMT(entry.external_attr >> 16)
        if not _safe_member(entry.filename) or not isinstance(record, dict):
            raise ValueError('案例 ZIP 包含无效路径或清单记录')
        if entry.is_dir() or mode not in (0, stat.S_IFREG):
            raise ValueError('案例 ZIP 只能包含普通文件')
        total += entry.file_size
        if total > config['unpacked_max_bytes'] or entry.file_size != record.get('size'):
            raise ValueError('案例 ZIP 解压大小与清单不符或超过 ' + config['unpacked_max_label'])
        _extract_verified_entry(source, entry, record, folder, config['unpacked_max_bytes'])


def _extract_verified_entry(source, entry, record, folder, maximum_unpacked):
    target = folder / entry.filename
    target.parent.mkdir(parents=True, exist_ok=True)
    digest, written = hashlib.sha256(), 0
    with source.open(entry) as incoming, target.open('wb') as output:
        while chunk := incoming.read(1024 * 1024):
            written += len(chunk)
            if written > entry.file_size or written > maximum_unpacked:
                raise ValueError('案例 ZIP 实际解压大小超过清单或允许上限')
            digest.update(chunk)
            output.write(chunk)
    if written != entry.file_size:
        raise ValueError('案例 ZIP 实际文件大小与清单不符：' + entry.filename)
    expected_hash = record.get('sha256')
    if not isinstance(expected_hash, str) or digest.hexdigest() != expected_hash:
        raise ValueError('案例 ZIP 文件 SHA256 校验失败：' + entry.filename)


def _validate_uploaded_dataset(folder, suite_id, expected):
    from runner.cloud import checked_instances
    dataset = folder / 'dataset.jsonl'
    cases = checked_instances(dataset, suite_id)
    if len(cases) > 10000:
        raise ValueError('单个案例集最多包含 10000 例')
    repo_slugs = {case.repo.replace('/', '__'): case.repo for case in cases}
    if len(repo_slugs) != len({case.repo for case in cases}):
        raise ValueError('仓库名转换后发生冲突，请调整案例数据的仓库路径')
    slugs = set(repo_slugs)
    bundle_files = {Path(name).stem for name in expected if name.startswith('bundles/')}
    if slugs != bundle_files:
        raise ValueError('案例清单中的仓库与 ZIP 内 Git bundle 不一致')
    return cases, dataset, slugs


def _prepare_repositories(cases, bundle_root, repos_root):
    repos_root = Path(repos_root)
    if repos_root.is_symlink():
        raise ValueError('案例仓库目录不能为符号链接')
    repos_root.mkdir(parents=True, exist_ok=True)
    by_slug = {}
    for case in cases:
        by_slug.setdefault(case.repo.replace('/', '__'), []).append(case)
    for slug, repo_cases in by_slug.items():
        _prepare_one_repository(slug, repo_cases, bundle_root, repos_root)


def _prepare_one_repository(slug, cases, bundle_root, repos_root):
    bundle = Path(bundle_root) / (slug + '.bundle')
    destination = repos_root / slug
    if destination.is_symlink():
        raise ValueError('已有案例仓库不能为符号链接')
    if destination.exists():
        _update_existing_repository(slug, cases, bundle, destination)
    else:
        _clone_repository(slug, bundle, destination)
    _validate_repository_cases(cases, destination)


def _clone_repository(slug, bundle, destination):
    try:
        result = subprocess.run(['git', 'clone', '--no-checkout', str(bundle), str(destination)],
                                capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        shutil.rmtree(destination, ignore_errors=True)
        raise ValueError('Git bundle 导入超时：' + slug) from None
    if result.returncode:
        shutil.rmtree(destination, ignore_errors=True)
        raise ValueError('Git bundle 导入失败：' + slug)


def _update_existing_repository(slug, cases, bundle, destination):
    result = subprocess.run(['git', '-C', str(destination), 'rev-parse', '--git-dir'],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError('已有案例仓库无效：' + slug)
    commits = {commit for item in cases for commit in (item.base_commit, item.head_commit)}
    try:
        if any(subprocess.run(['git', '-C', str(destination), 'cat-file', '-e', commit + '^{commit}'],
                              capture_output=True, timeout=30).returncode for commit in commits):
            fetched = subprocess.run(['git', '-C', str(destination), 'fetch', '--no-tags', str(bundle),
                                      '+refs/*:refs/bench-case-upload/*'],
                                     capture_output=True, text=True, timeout=600)
            if fetched.returncode:
                raise ValueError('无法将新提交导入已有案例仓库：' + slug)
    except subprocess.TimeoutExpired:
        raise ValueError('准备案例 Git 历史超时：' + slug) from None


def _validate_repository_cases(cases, destination):
    for item in cases:
        for commit in (item.base_commit, item.head_commit):
            result = subprocess.run(['git', '-C', str(destination), 'cat-file', '-e', commit + '^{commit}'],
                                    capture_output=True, timeout=30)
            if result.returncode:
                raise ValueError(f'Git bundle 缺少案例提交：{item.instance_id}')
        result = subprocess.run(['git', '-C', str(destination), 'merge-base', item.base_commit, item.head_commit],
                                capture_output=True, timeout=30)
        if result.returncode:
            raise ValueError(f'案例提交没有共同历史：{item.instance_id}')


def _install_custom(dashboard, archive):
    from .service import save
    with tempfile.TemporaryDirectory(prefix='case-prepare-', dir=dashboard.data) as folder_name:
        folder = Path(folder_name)
        manifest, suite_id, cases, dataset, slugs = _extract_custom(archive, folder)
        digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
        registry = _registry(dashboard.data)
        existing = next((row for row in registry['datasets']
                         if row.get('suite') == suite_id and row.get('dataset_sha256') == digest), None)
        if existing:
            return dict(status(dashboard.root, dashboard.data), message=f"案例集“{existing['name']}”已导入，可直接选择使用。")
        _prepare_repositories(cases, folder / 'bundles', dashboard.data / 'repos')
        identifier = digest[:16]
        upload_rel = f'case-datasets/{suite_id}/{identifier}/dataset.jsonl'
        destination = dashboard.data / upload_rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        dataset.replace(destination)
        registry['datasets'].append(dict(id=f'uploads/{suite_id}/{identifier}.jsonl', suite=suite_id,
                                        name=manifest['name'].strip(), count=len(cases),
                                        dataset_sha256=digest, file=upload_rel,
                                        repositories=sorted(slugs)))
        save(_registry_path(dashboard.data), registry)
    return status(dashboard.root, dashboard.data)


def install(dashboard, archive):
    from runner.locking import acquire
    with (dashboard.state / 'case-upload.lock').open('a+b') as lock:
        try:
            acquire(lock)
        except BlockingIOError:
            raise ValueError('已有案例上传正在处理，请稍后刷新') from None
        with dashboard.lock:
            return _install_locked(dashboard, archive)


def _install_locked(dashboard, archive):
    from runner.cloud import checked_instances
    from tools.prepare_case_archive import stage
    from tools.setup import restore_repositories
    from .service import save
    root, data = dashboard.root, dashboard.data
    jobs = dashboard.queue.jobs() if hasattr(dashboard, 'queue') else dashboard.jobs
    if any(job['status'] in {'queued', 'running', 'cancelling'} for job in jobs.values()):
        raise ValueError('请先等待测评任务结束，再准备案例')
    config = limits()
    if archive.stat().st_size > config['upload_max_bytes']:
        raise ValueError('案例 ZIP 上限为 ' + config['upload_max_label'])
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > MAX_FILES or sum(entry.file_size for entry in entries) > config['unpacked_max_bytes']:
            raise ValueError('案例 ZIP 文件过多或解压总大小超过 ' + config['unpacked_max_label'])
        custom = 'manifest.json' in source.namelist()
    if custom:
        return _install_custom(dashboard, archive)
    manifest_path = root / 'deployment/aacr-cases-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    cases = checked_instances(root / 'data/pilot5.jsonl', 'aacr')
    repositories = sorted({case.repo.replace('/', '__') for case in cases})
    target = data / 'repos'
    if target.is_symlink():
        raise ValueError('案例仓库目录不能为符号链接')
    target.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='case-prepare-', dir=data) as folder:
        staging = Path(folder)
        stage(archive, manifest, destination_root=staging)
        restore_repositories(cases, bundle_root=staging / 'bundles', repos_root=staging / 'repos')
        for slug in repositories:
            destination = target / slug
            if destination.exists():
                existing = [case for case in cases if case.repo.replace('/', '__') == slug]
                restore_repositories(existing, bundle_root=staging / 'bundles', repos_root=target)
        for slug in repositories:
            if not (target / slug).exists():
                (staging / 'repos' / slug).replace(target / slug)
    save(data / 'case-assets.json', dict(
        manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        repositories=repositories))
    return status(root, data)
