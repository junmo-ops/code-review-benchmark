"""Validate and register repeatable benchmark case uploads without model calls."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile

UPLOAD_FORMAT = 'code-review-benchmark-cases-v1'
CASE_CONFIG_FORMAT = 'code-review-benchmark-case-config-v1'
REPOSITORY_FORMAT = 'code-review-benchmark-repositories-v1'
MAX_FILES = 10002
MAX_REPOSITORY_ARCHIVE_FILES = 250000
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
        try:
            if isinstance(row, dict) and isinstance(row.get('file'), str) and _registered_row_usable(row, data, base):
                result.append(row)
        except (OSError, ValueError):
            continue
    return result


def _registered_row_usable(row, data, base):
    from .case_import import dataset_pending
    if dataset_pending(data, row.get('dataset_sha256')):
        return False
    original = Path(data) / row['file']
    path = original.resolve()
    if not path.is_relative_to(base) or not path.is_file() or original.is_symlink():
        return False
    repositories = row.get('repositories')
    if not isinstance(repositories, list) or not repositories:
        return False
    if not all(isinstance(slug, str) and re.fullmatch(r'[A-Za-z0-9_.-]+', slug) for slug in repositories):
        return False
    repositories_ready = all((Path(data) / 'repos' / slug / '.git').is_dir() for slug in repositories)
    record_path = (Path(data) / 'case-assets' / 'records' / row.get('suite', '')
                   / (str(row.get('dataset_sha256', '')) + '.json'))
    from .case_storage import mode
    assets_ready = mode() == 'cos' and record_path.is_file()
    return repositories_ready or assets_ready


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
    repositories = repository_entries(data)
    return dict(enabled=True, ready=ready, name='案例集导入', message=message, **limits(),
                repositories=repositories,
                datasets=[{'id': item.get('id'), 'name': item.get('name'), 'suite': item.get('suite'),
                           'count': item.get('count'), 'dataset_sha256': item.get('dataset_sha256')}
                          for item in datasets])


def repository_registry_path(data):
    return Path(data) / 'case-repositories' / 'registry.json'


def repository_registry(data):
    try:
        value = json.loads(repository_registry_path(data).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) and isinstance(value.get('repositories'), dict) else {'repositories': {}}
    except (OSError, ValueError):
        return {'repositories': {}}


def repository_entries(data):
    result = []
    for slug, row in repository_registry(data)['repositories'].items():
        if (isinstance(slug, str) and re.fullmatch(r'[A-Za-z0-9_.-]+', slug)
                and isinstance(row, dict) and isinstance(row.get('repo'), str)
                and isinstance(row.get('versions'), list)):
            versions = [item for item in row['versions'] if isinstance(item, dict)
                        and isinstance(item.get('sha256'), str)
                        and re.fullmatch(r'[a-f0-9]{64}', item['sha256'])
                        and type(item.get('size')) is int and item['size'] > 0]
            result.append(dict(slug=slug, repo=row['repo'], versions=[
                {'sha256': item['sha256'], 'size': item['size'],
                 'current': item['sha256'] == row.get('current')}
                for item in versions]))
    return sorted(result, key=lambda item: item['repo'].lower())


def manage_case_dataset(dashboard, request):
    return _with_resource_lock(dashboard, lambda: _manage_case_dataset_locked(dashboard, request))


def manage_repository(dashboard, request):
    return _with_resource_lock(dashboard, lambda: _manage_repository_locked(dashboard, request))


def _with_resource_lock(dashboard, operation):
    from .case_import import ensure_idle
    ensure_idle(dashboard)
    from runner.locking import acquire, release
    lock_path = dashboard.state / 'case-upload.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+b') as lock:
        try:
            acquire(lock)
        except BlockingIOError:
            raise ValueError('已有案例资源操作正在处理，请稍后重试') from None
        try:
            with dashboard.lock:
                ensure_idle(dashboard)
                return operation()
        finally:
            release(lock)


def _manage_case_dataset_locked(dashboard, request):
    _ensure_no_active_jobs(dashboard)
    action, identifier = request.get('action'), request.get('id')
    registry = _registry(dashboard.data)
    row = next((item for item in registry['datasets'] if item.get('id') == identifier), None)
    if not row or not isinstance(identifier, str) or not identifier.startswith('uploads/'):
        raise ValueError('只能维护网页导入的案例集')
    if action == 'rename':
        return _rename_dataset(dashboard, registry, row, request.get('name'))
    if action == 'delete':
        return _delete_dataset(dashboard, registry, row)
    raise ValueError('不支持的案例集操作')


def _rename_dataset(dashboard, registry, row, name):
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
        raise ValueError('案例集名称须为 1–100 个字符')
    row['name'] = name.strip()
    from .service import save
    if mode_is_cos():
        from .case_storage import CaseAssetStorage
        CaseAssetStorage(dashboard.data).rename_dataset(row['suite'], row['dataset_sha256'], row['name'])
    save(_registry_path(dashboard.data), registry)
    return dict(message='案例集名称已更新。')


def _delete_dataset(dashboard, registry, row):
    _ensure_dataset_unused(dashboard.data, row['dataset_sha256'])
    dataset = Path(dashboard.data) / row['file']
    base = (Path(dashboard.data) / 'case-datasets').resolve()
    if dataset.is_symlink() or not dataset.resolve().is_relative_to(base):
        raise ValueError('案例集文件路径无效')
    if mode_is_cos():
        from .case_storage import CaseAssetStorage
        CaseAssetStorage(dashboard.data).delete_dataset(row['suite'], row['dataset_sha256'])
    dataset.unlink(missing_ok=True)
    record = Path(dashboard.data) / 'case-assets' / 'records' / row['suite'] / (row['dataset_sha256'] + '.json')
    record.unlink(missing_ok=True)
    registry['datasets'].remove(row)
    from .service import save
    save(_registry_path(dashboard.data), registry)
    return dict(message='案例集已删除。')


def _ensure_dataset_unused(data, digest):
    for path in (Path(data) / 'runs').rglob('run-config.json'):
        try:
            config = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(config, dict) and config.get('dataset_sha256') == digest:
            raise ValueError('该案例集已被测评批次引用，请保留它以便后续评分和恢复')


def _manage_repository_locked(dashboard, request):
    _ensure_no_active_jobs(dashboard)
    slug, action = request.get('slug'), request.get('action')
    if not isinstance(slug, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', slug):
        raise ValueError('仓库标识无效')
    registry = repository_registry(dashboard.data)
    row = registry['repositories'].get(slug)
    if not isinstance(row, dict) or not isinstance(row.get('versions'), list):
        raise ValueError('仓库资源不存在')
    if action != 'delete':
        raise ValueError('仓库更新请上传新版本资源 ZIP')
    if _repository_is_referenced(dashboard.data, slug):
        raise ValueError('该仓库仍被案例集引用，请先删除相关案例集')
    repos = Path(dashboard.data) / 'repos' / slug
    if repos.is_symlink():
        raise ValueError('本地仓库目录不能是符号链接')
    versions = row['versions']
    registry['repositories'].pop(slug)
    if mode_is_cos():
        from .case_storage import CaseAssetStorage
        CaseAssetStorage(dashboard.data).delete_repository(slug, versions, registry)
    from .service import save
    save(repository_registry_path(dashboard.data), registry)
    _remove_unreferenced_repository_files(dashboard.data, versions, registry)
    shutil.rmtree(repos, ignore_errors=True)
    return dict(message='仓库资源及其未被引用的版本已删除。')


def _repository_is_referenced(data, slug):
    return any(isinstance(item, dict) and slug in item.get('repositories', [])
               for item in _registry(data)['datasets'])


def _remove_unreferenced_repository_files(data, versions, registry):
    remaining_hashes = {version.get('sha256') for repo in registry['repositories'].values()
                        for version in repo.get('versions', []) if isinstance(version, dict)}
    for item in versions:
        if item.get('sha256') in remaining_hashes:
            continue
        path = Path(data) / item.get('file', '')
        base = (Path(data) / 'case-repositories' / 'objects').resolve()
        if path.is_symlink() or not path.resolve().is_relative_to(base):
            continue
        path.unlink(missing_ok=True)


def mode_is_cos():
    from .case_storage import mode
    return mode() == 'cos'


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
    cases, dataset, slugs = _validate_uploaded_dataset(folder, suite_id, expected, manifest)
    return manifest, suite_id, cases, dataset, slugs


def _validate_archive_manifest(source, entries, names):
    _validate_manifest_entry_list(entries, names)
    info = _manifest_entry(entries)
    manifest = _decode_manifest(source, info)
    _validate_manifest_identity(manifest)
    suite_id = _validate_manifest_suite(manifest)
    expected = _validate_manifest_files(manifest, names)
    return manifest, suite_id, expected


def _validate_manifest_entry_list(entries, names):
    invalid = len(entries) > MAX_FILES or len(names) != len(set(names)) or 'manifest.json' not in names
    if invalid:
        raise ValueError('案例 ZIP 缺少清单、含重复文件或文件过多')


def _manifest_entry(entries):
    info = next(entry for entry in entries if entry.filename == 'manifest.json')
    if info.is_dir() or info.file_size > 8 * 1024 * 1024:
        raise ValueError('案例 ZIP 的 manifest.json 无效或过大')
    return info


def _decode_manifest(source, info):
    try:
        return json.loads(source.read(info).decode('utf-8-sig'))
    except (UnicodeDecodeError, ValueError, KeyError):
        raise ValueError('案例 ZIP 的 manifest.json 无效') from None


def _validate_manifest_identity(manifest):
    if (not isinstance(manifest, dict) or manifest.get('format') not in {UPLOAD_FORMAT, CASE_CONFIG_FORMAT}
            or not isinstance(manifest.get('name'), str) or not manifest['name'].strip()
            or len(manifest['name']) > 100):
        raise ValueError('案例 ZIP 格式或案例集名称无效')


def _validate_manifest_suite(manifest):
    suite_id = manifest.get('suite')
    if not isinstance(suite_id, str) or suite_id not in {'aacr', 'swe_prbench_llm', 'codefuse_cr'}:
        raise ValueError('网页上传当前支持 AACR、SWE-PRBench 和 CodeFuse 案例集')
    from suites.registry import get_suite
    get_suite(suite_id)
    return suite_id


def _validate_manifest_files(manifest, names):
    expected = manifest.get('files')
    if not isinstance(expected, dict) or set(expected) != set(names) - {'manifest.json'}:
        raise ValueError('案例 ZIP 文件与 manifest.json 清单不匹配')
    if 'dataset.jsonl' not in expected:
        raise ValueError('案例 ZIP 必须包含 dataset.jsonl')
    if manifest['format'] == CASE_CONFIG_FORMAT and set(expected) != {'dataset.jsonl'}:
        raise ValueError('纯案例配置包只能包含 dataset.jsonl，不应携带 Git bundle')
    return expected


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


def _validate_uploaded_dataset(folder, suite_id, expected, manifest):
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
    if manifest['format'] == CASE_CONFIG_FORMAT:
        references = manifest.get('repositories')
        if (not isinstance(references, list)
                or any(not isinstance(slug, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', slug)
                       for slug in references)
                or set(references) != slugs):
            raise ValueError('案例配置包中的仓库引用与案例清单不一致')
    elif slugs != bundle_files:
        raise ValueError('案例清单中的仓库与 ZIP 内 Git bundle 不一致')
    return cases, dataset, slugs


def _notify(progress, phase, message, completed=0, total=0, **details):
    if progress:
        progress(phase, message, completed, total, **details)


def _case_only_repositories(dashboard, cases, folder, slugs, progress=None):
    registry = repository_registry(dashboard.data)['repositories']
    staging = Path(folder) / 'bundles'
    versions = {}
    for index, slug in enumerate(sorted(slugs)):
        _notify(progress, 'download', f'准备仓库资源 {index + 1}/{len(slugs)}：{slug}', index, len(slugs))
        record = _current_repository_record(registry, slug)
        bundle = _available_repository_bundle(dashboard.data, slug, record)
        target = staging / (slug + '.bundle')
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(bundle, target)
        except OSError:
            shutil.copyfile(bundle, target)
        versions[slug] = {'sha256': record['sha256'], 'size': record['size'], 'source': 'repository-pool'}
    _prepare_repositories(cases, staging, Path(dashboard.data) / 'repos', progress)
    return versions


def _current_repository_record(registry, slug):
    entry = registry.get(slug)
    if not isinstance(entry, dict):
        raise ValueError('案例引用的仓库尚未上传：' + slug)
    record = next((item for item in entry.get('versions', [])
                   if item.get('sha256') == entry.get('current')), None)
    if not record:
        raise ValueError('案例引用的仓库尚未上传：' + slug)
    return record


def _available_repository_bundle(data, slug, record):
    from .case_storage import CaseAssetStorage, mode
    bundle = Path(data) / 'case-repositories' / 'objects' / (record['sha256'] + '.bundle')
    if (bundle.is_file() and not bundle.is_symlink() and bundle.stat().st_size == record['size']
            and _hash_file(bundle) == record['sha256']):
        return bundle
    if mode() != 'cos':
        raise ValueError('本地仓库 bundle 缺失或校验失败：' + slug)
    return CaseAssetStorage(data).restore_repository_bundle(slug, record)


def _register_repository_versions(data, manifest, bundle_root, slugs):
    from .service import save
    registry = repository_registry(data)
    object_root = Path(data) / 'case-repositories' / 'objects'
    object_root.mkdir(parents=True, exist_ok=True)
    for slug in slugs:
        record = manifest['files'][f'bundles/{slug}.bundle']
        repo_name = manifest.get('repositories', {}).get(slug, {}).get('name') or slug.replace('__', '/')
        source = Path(bundle_root) / (slug + '.bundle')
        target = object_root / (record['sha256'] + '.bundle')
        if not target.is_file():
            shutil.copyfile(source, target)
        entry = registry['repositories'].setdefault(slug, {'repo': repo_name, 'versions': [], 'current': ''})
        version = {'sha256': record['sha256'], 'size': record['size'],
                   'file': f'case-repositories/objects/{record["sha256"]}.bundle'}
        if not any(item.get('sha256') == version['sha256'] for item in entry['versions']):
            entry['versions'].append(version)
        entry['current'] = version['sha256']
    save(repository_registry_path(data), registry)


def _prepare_repositories(cases, bundle_root, repos_root, progress=None):
    repos_root = Path(repos_root)
    if repos_root.is_symlink():
        raise ValueError('案例仓库目录不能为符号链接')
    repos_root.mkdir(parents=True, exist_ok=True)
    by_slug = {}
    for case in cases:
        by_slug.setdefault(case.repo.replace('/', '__'), []).append(case)
    completed = 0
    for index, (slug, repo_cases) in enumerate(sorted(by_slug.items())):
        _notify(progress, 'check', f'校验仓库 {index + 1}/{len(by_slug)}：{slug}（已校验 {completed}/{len(cases)} 例）', completed, len(cases))
        _prepare_one_repository(slug, repo_cases, bundle_root, repos_root)
        completed += len(repo_cases)
        _notify(progress, 'check', f'已校验 {completed}/{len(cases)} 例', completed, len(cases))


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
            namespace = 'refs/bench-case-upload/' + _hash_file(bundle)
            fetched = subprocess.run(['git', '-C', str(destination), 'fetch', '--no-tags', str(bundle),
                                      '+refs/*:' + namespace + '/*'],
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


def _install_custom(dashboard, archive, progress=None):
    from .service import save
    with tempfile.TemporaryDirectory(prefix='case-prepare-', dir=dashboard.data) as folder_name:
        folder = Path(folder_name)
        manifest, suite_id, cases, dataset, slugs = _extract_custom(archive, folder)
        digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
        registry = _registry(dashboard.data)
        previous = any(row.get('dataset_sha256') == digest for row in registry['datasets'])
        _notify(progress, 'validate', f'案例包校验完成：{len(cases)} 例，{len(slugs)} 个仓库',
                dataset_sha256=digest, previously_registered=previous)
        # Always merge the bundle into the local cache. On a clean container
        # restart the same dataset can already be registered while its Git
        # repository still needs to be restored from the archive.
        if manifest['format'] == CASE_CONFIG_FORMAT:
            repository_versions = _case_only_repositories(dashboard, cases, folder, slugs, progress)
        else:
            _prepare_repositories(cases, folder / 'bundles', dashboard.data / 'repos', progress)
            _register_repository_versions(dashboard.data, manifest, folder / 'bundles', slugs)
            repository_versions = {slug: {'sha256': manifest['files'][f'bundles/{slug}.bundle']['sha256'],
                                           'size': manifest['files'][f'bundles/{slug}.bundle']['size'],
                                           'source': 'case-archive'} for slug in slugs}
        existing = next((row for row in registry['datasets']
                         if row.get('suite') == suite_id and row.get('dataset_sha256') == digest), None)
        if existing:
            return dict(status(dashboard.root, dashboard.data), message=f"案例集“{existing['name']}”已导入，可直接选择使用。")
        identifier = digest
        upload_rel = f'case-datasets/{suite_id}/{identifier}/dataset.jsonl'
        destination = dashboard.data / upload_rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        dataset.replace(destination)
        registry['datasets'].append(dict(id=f'uploads/{suite_id}/{identifier}.jsonl', suite=suite_id,
                                        name=manifest['name'].strip(), count=len(cases),
                                        dataset_sha256=digest, file=upload_rel,
                                        repositories=sorted(slugs), repository_versions=repository_versions))
        save(_registry_path(dashboard.data), registry)
    return dict(status(dashboard.root, dashboard.data), message=f"案例集“{manifest['name']}”已导入，可新建测评。")


def install(dashboard, archive, persist=True, progress=None):
    from runner.locking import acquire
    with (dashboard.state / 'case-upload.lock').open('a+b') as lock:
        try:
            acquire(lock)
        except BlockingIOError:
            raise ValueError('已有案例上传正在处理，请稍后刷新') from None
        with dashboard.lock:
            return _install_transaction(dashboard, archive, persist, progress)


def _install_transaction(dashboard, archive, persist, progress):
    path = _registry_path(dashboard.data)
    previous = path.read_bytes() if path.is_file() else None
    try:
        result = _install_locked(dashboard, archive, progress)
        if persist:
            from .case_storage import persist_case_upload
            _notify(progress, 'persist', '案例提交已校验，正在保存配置到存储')
            persist_case_upload(dashboard.data, archive)
        return result
    except Exception:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            from .service import save
            save(path, json.loads(previous))
        raise


def install_repositories(dashboard, archive):
    from .case_import import ensure_idle
    ensure_idle(dashboard)
    from runner.locking import acquire
    with (dashboard.state / 'case-upload.lock').open('a+b') as lock:
        try:
            acquire(lock)
        except BlockingIOError:
            raise ValueError('已有案例资源上传正在处理，请稍后刷新') from None
        with dashboard.lock:
            ensure_idle(dashboard)
            path = repository_registry_path(dashboard.data)
            previous = path.read_bytes() if path.is_file() else None
            result, versions = _install_repository_archive(dashboard, archive)
            from .case_storage import persist_repository_upload
            try:
                persist_repository_upload(dashboard.data, versions)
            except RuntimeError as error:
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(previous)
                raise ValueError(str(error)) from None
            return result


def _install_repository_archive(dashboard, archive):
    _ensure_no_active_jobs(dashboard)
    from .case_upload import limits
    maximum = limits()
    if archive.stat().st_size > maximum['upload_max_bytes']:
        raise ValueError('仓库 ZIP 上限为 ' + maximum['upload_max_label'])
    with zipfile.ZipFile(archive) as source:
        registry = repository_registry(dashboard.data)
        object_root = Path(dashboard.data) / 'case-repositories' / 'objects'
        object_root.mkdir(parents=True, exist_ok=True)
        manifest = _optional_repository_manifest(source)
        versions = (_import_bundle_archive(source, manifest, object_root, maximum)
                    if manifest else _import_git_repository_archive(source, dashboard.data, object_root, maximum))
        for slug, repo, record in versions:
            _register_repository_version(registry, slug, repo, record)
        from .service import save
        save(repository_registry_path(dashboard.data), registry)
    result = dict(message='仓库资源已导入，可供案例配置引用。', repositories=repository_entries(dashboard.data))
    return result, {slug: record for slug, _, record in versions}


def _import_bundle_archive(source, manifest, object_root, maximum):
    files = manifest['files']
    versions, total = [], 0
    for slug, repo in manifest['repositories'].items():
        record = _repository_record(slug, repo, files)
        member = source.getinfo(f'bundles/{slug}.bundle')
        total += member.file_size
        if member.file_size != record['size'] or total > maximum['unpacked_max_bytes']:
            raise ValueError('仓库 ZIP 解压大小与清单不符或超出上限')
        target = _extract_repository_bundle(source, slug, member, record, object_root, maximum)
        _verify_repository_bundle(target, slug)
        versions.append((slug, repo['name'], record))
    return versions


def _import_git_repository_archive(source, data, object_root, maximum):
    entries = source.infolist()
    total = sum(item.file_size for item in entries)
    names = [item.filename for item in entries]
    if len(names) != len(set(names)) or len(names) > MAX_REPOSITORY_ARCHIVE_FILES:
        raise ValueError('仓库 ZIP 含重复路径或文件过多')
    if total > maximum['unpacked_max_bytes']:
        raise ValueError('仓库 ZIP 解压总大小超过 ' + maximum['unpacked_max_label'])
    with tempfile.TemporaryDirectory(prefix='repo-import-', dir=data) as temporary:
        root = Path(temporary) / 'source'
        _extract_repository_tree(source, entries, root)
        repositories = _find_git_repositories(root)
        if not repositories:
            raise ValueError('ZIP 中没有找到带 .git 目录的 Git 仓库')
        return [_bundle_repository(path, object_root) for path in repositories]


def _extract_repository_tree(source, entries, root):
    extracted = set()
    repositories = {}
    for entry in entries:
        parts = _repository_member_parts(entry)
        if parts is None:
            continue
        normalized = '/'.join(parts)
        if normalized in extracted:
            raise ValueError('仓库 ZIP 含重复路径')
        extracted.add(normalized)
        git_index = next((index for index, part in enumerate(parts) if part.lower() == '.git'), None)
        if git_index is None:
            continue
        repository = tuple(parts[:git_index])
        if repository not in repositories:
            repositories[repository] = root / ('r' + str(len(repositories)))
        target = repositories[repository].joinpath('.git', *parts[git_index + 1:])
        if entry.is_dir() or entry.filename.endswith('\\'):
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open(entry) as reader, target.open('wb') as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)


def _repository_member_parts(entry):
    relative = entry.filename.replace('\\', '/')
    is_directory = entry.is_dir() or relative.endswith('/')
    while relative.startswith('./'):
        relative = relative[2:]
    if relative in {'', '.'} and is_directory:
        return None
    parts = relative.rstrip('/').split('/')
    mode = stat.S_IFMT(entry.external_attr >> 16)
    invalid_path = (not relative or relative.startswith('/')
                    or any(part in {'', '.', '..'} or ':' in part for part in parts))
    if invalid_path:
        raise ValueError('仓库 ZIP 含无效路径：' + entry.filename)
    if mode == stat.S_IFLNK:
        if any(part.lower() == '.git' for part in parts):
            raise ValueError('仓库 ZIP 的 .git 历史含符号链接，请上传完整克隆的仓库')
        return None
    valid_type = mode in (0, stat.S_IFDIR) if is_directory else mode in (0, stat.S_IFREG)
    if not valid_type:
        raise ValueError('仓库 ZIP 含不支持的链接或特殊文件：' + entry.filename)
    return parts


def _find_git_repositories(root):
    result = []
    for folder, directories, _ in os.walk(root):
        if '.git' in directories:
            result.append(Path(folder))
            directories[:] = []
    return result


def _bundle_repository(path, object_root):
    name = _repository_name_from_origin(path)
    slug = name.replace('/', '__')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', slug):
        raise ValueError('仓库 origin 中的 owner/repo 名称不受支持：' + path.name)
    with tempfile.TemporaryDirectory(prefix='repo-bundle-') as temporary:
        bundle = Path(temporary) / (slug + '.bundle')
        try:
            shallow = subprocess.run(['git', '-C', str(path), 'rev-parse', '--is-shallow-repository'],
                                     capture_output=True, text=True, timeout=30)
            if shallow.returncode or shallow.stdout.strip() == 'true':
                raise ValueError('仓库是浅克隆或 Git 状态不可用，请上传完整克隆：' + name)
            environment = os.environ.copy()
            environment['GIT_NO_LAZY_FETCH'] = '1'
            created = subprocess.run(['git', '-C', str(path), 'bundle', 'create', str(bundle), '--all'],
                                     capture_output=True, text=True, timeout=3600, env=environment)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError('无法从上传仓库生成 Git 历史：' + name) from None
        except ValueError:
            raise
        if created.returncode:
            raise ValueError('无法从上传仓库生成 Git 历史：' + name)
        record = {'sha256': _hash_file(bundle), 'size': bundle.stat().st_size}
        target = object_root / (record['sha256'] + '.bundle')
        if not target.is_file() or target.stat().st_size != record['size'] or _hash_file(target) != record['sha256']:
            shutil.copyfile(bundle, target)
        _verify_repository_bundle(target, slug)
    return slug, name, record


def _repository_name_from_origin(path):
    try:
        result = subprocess.run(['git', '-C', str(path), 'remote', 'get-url', 'origin'],
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        result = None
    remote = result.stdout.strip().removesuffix('.git') if result and result.returncode == 0 else ''
    match = re.search(r'(?:^|[:/])([^/:]+)/([^/:]+)$', remote)
    if not match:
        raise ValueError('仓库缺少有效的 origin，无法识别 owner/repo：' + path.name)
    return match.group(1) + '/' + match.group(2)


def _optional_repository_manifest(source):
    names = source.namelist()
    if 'manifest.json' not in names:
        return None
    info = source.getinfo('manifest.json')
    if info.file_size > 8 * 1024 * 1024:
        return None
    try:
        manifest = json.loads(source.read(info).decode('utf-8-sig'))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(manifest, dict) or manifest.get('format') != REPOSITORY_FORMAT:
        return None
    _validate_repository_zip_entries(names, info)
    _validate_repository_manifest(manifest, names, source)
    return manifest


def _validate_repository_zip_entries(names, info):
    if info is None or len(names) != len(set(names)) or len(names) > MAX_FILES:
        raise ValueError('仓库 ZIP 清单缺失或无效')
    if info.file_size > 8 * 1024 * 1024:
        raise ValueError('仓库 ZIP 清单缺失或无效')


def _validate_repository_manifest(manifest, names, source):
    if (not isinstance(manifest, dict) or manifest.get('format') != REPOSITORY_FORMAT
            or not isinstance(manifest.get('repositories'), dict) or not manifest['repositories']):
        raise ValueError('仓库 ZIP 格式无效')
    files = manifest.get('files')
    expected = {'bundles/' + slug + '.bundle' for slug in manifest['repositories']}
    if (not isinstance(files, dict) or set(files) != set(names) - {'manifest.json'}
            or set(files) != expected or any(item.is_dir() for item in source.infolist())):
        raise ValueError('仓库 ZIP 文件与清单不匹配')


def _repository_record(slug, repo, files):
    record = files.get(f'bundles/{slug}.bundle')
    if (not isinstance(slug, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', slug)
            or not isinstance(repo, dict) or not isinstance(repo.get('name'), str)
            or not re.fullmatch(r'[^/]+/[^/]+', repo['name']) or repo['name'].replace('/', '__') != slug
            or not isinstance(record, dict) or type(record.get('size')) is not int or record['size'] <= 0
            or not isinstance(record.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', record['sha256'])):
        raise ValueError('仓库清单字段无效：' + str(slug))
    return record


def _extract_repository_bundle(source, slug, member, record, object_root, maximum):
    target = object_root / (record['sha256'] + '.bundle')
    valid = target.is_file() and target.stat().st_size == record['size'] and _hash_file(target) == record['sha256']
    if not valid:
        temporary = target.with_suffix('.part')
        _extract_verified_entry(source, member, record, temporary.parent, maximum['unpacked_max_bytes'])
        extracted = temporary.parent / f'bundles/{slug}.bundle'
        extracted.replace(temporary)
        temporary.replace(target)
    return target


def _verify_repository_bundle(target, slug):
    try:
        with tempfile.TemporaryDirectory(prefix='bundle-verify-') as temporary:
            initialized = subprocess.run(['git', 'init', '--bare', '--quiet', temporary],
                                         capture_output=True, timeout=30)
            if initialized.returncode:
                raise ValueError
            result = subprocess.run(['git', '-C', temporary, 'bundle', 'verify', str(target)],
                                    capture_output=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('Git bundle 校验失败：' + slug) from None
    except ValueError:
        raise ValueError('Git bundle 校验失败：' + slug) from None
    if result.returncode:
        raise ValueError('Git bundle 无效或不包含完整历史：' + slug)


def _register_repository_version(registry, slug, name, record):
    row = registry['repositories'].setdefault(slug, {'repo': name, 'versions': [], 'current': ''})
    if row.get('repo') != name or not isinstance(row.get('versions'), list):
        raise ValueError('仓库名称与已有资源不一致：' + slug)
    version = {'sha256': record['sha256'], 'size': record['size'],
               'file': f'case-repositories/objects/{record["sha256"]}.bundle'}
    if not any(item.get('sha256') == version['sha256'] for item in row['versions']):
        row['versions'].append(version)
    row['current'] = version['sha256']


def _hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _install_locked(dashboard, archive, progress=None):
    _ensure_no_active_jobs(dashboard)
    custom = _validate_case_archive(archive)
    if custom:
        return _install_custom(dashboard, archive, progress)
    return _install_legacy_archive(dashboard, archive)


def _ensure_no_active_jobs(dashboard):
    jobs = dashboard.queue.jobs() if hasattr(dashboard, 'queue') else dashboard.jobs
    if any(job['status'] in {'queued', 'running', 'cancelling'} for job in jobs.values()):
        raise ValueError('请先等待测评任务结束，再准备案例')


def _validate_case_archive(archive):
    config = limits()
    if archive.stat().st_size > config['upload_max_bytes']:
        raise ValueError('案例 ZIP 上限为 ' + config['upload_max_label'])
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > MAX_FILES or sum(entry.file_size for entry in entries) > config['unpacked_max_bytes']:
            raise ValueError('案例 ZIP 文件过多或解压总大小超过 ' + config['unpacked_max_label'])
        return 'manifest.json' in source.namelist()


def _install_legacy_archive(dashboard, archive):
    from runner.cloud import checked_instances
    from tools.prepare_case_archive import stage
    from tools.setup import restore_repositories
    from .service import save
    root, data = dashboard.root, dashboard.data
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
