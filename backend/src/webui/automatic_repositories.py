"""Resolve Git sources and prepare commit-addressed repositories on demand."""
import os
from contextlib import contextmanager
from pathlib import Path
import tempfile
from types import SimpleNamespace

from .git_import import validate_url, prepare


def sources(cases):
    base = os.environ.get('BENCH_GIT_BASE_URL', 'https://gitee-proxy.paasst.cmbchina.cn').rstrip('/')
    result = {}
    for case in cases:
        url, name = validate_url(base + '/' + case.repo + '.git')
        if name != case.repo:
            raise ValueError('案例仓库名须为组织/项目名：' + case.repo)
        result[case.repo.replace('/', '__')] = {'source': 'git', 'url': url}
    return result


def valid(slug, item):
    try:
        _, name = validate_url(item.get('url'))
        return name.replace('/', '__') == slug
    except (ValueError, TypeError):
        return False


def prepare_cases(data, cases, references, progress=None):
    from .case_assets import _validate_repository_cases, _prepare_one_repository
    from runner.git_bundles import create_complete_bundle
    data = Path(data)
    proxy = SimpleNamespace(data=data)
    by_slug = {}
    for case in cases:
        by_slug.setdefault(case.repo.replace('/', '__'), []).append(case)
    notify = progress or (lambda *args: None)
    with locked(data / 'ui-state' / 'automatic-git.lock'):
        for slug, items in by_slug.items():
            source = references[slug]
            label = 'Git' if source.get('source') == 'git' else 'COS'
            notify('source', f'仓库来源：{slug} → {label}')
            destination = data / 'repos' / slug
            if ready(destination, items, _validate_repository_cases):
                notify('ready', '复用已校验的案例提交：' + slug)
                continue
            if source.get('source') != 'git':
                prepare_cos(data, slug, items, source, notify)
                continue
            if not valid(slug, source):
                raise ValueError('案例 Git 地址与仓库标识不一致：' + slug)
            repo = prepare(proxy, source['url'], notify)
            _validate_repository_cases(items, repo)
            with tempfile.TemporaryDirectory(prefix='automatic-git-', dir=data) as temporary:
                folder = Path(temporary)
                create_complete_bundle(repo, folder / (slug + '.bundle'))
                _prepare_one_repository(slug, items, folder, data / 'repos')
            notify('ready', '案例提交准备完成：' + slug)


def ready(path, cases, validate):
    if not (path / '.git').is_dir():
        return False
    try:
        validate(cases, path)
        return True
    except ValueError:
        return False


def prepare_selected(dashboard, spec, key, action, progress):
    if action in {'report', 'export', 'eval', 'recover'}:
        return False
    from .case_assets import _registry
    from runner.cloud import checked_instances
    row, suite, digest = dashboard._selected_case_asset(_registry(dashboard.data)['datasets'], spec, key)
    from .repository_sources import binding, valid_records
    pinned = binding(dashboard, spec, key)
    if pinned:
        digest = pinned['dataset_sha256']
        refs = pinned['repositories']
    else:
        refs = row.get('repository_versions', {}) if row else {}
    if suite not in {'aacr', 'swe_prbench_llm', 'codefuse_cr'} or not refs:
        return False
    if not valid_records(refs):
        raise ValueError('案例仓库来源配置无效')
    dataset = dashboard.dataset_path(spec['dataset']) if spec else dashboard.run_dir(key) / 'dataset.jsonl'
    from .case_storage import _sha256
    if _sha256(dataset) != digest:
        raise ValueError('冻结案例清单校验失败；禁止按其他案例恢复仓库')
    cases = checked_instances(dataset, suite)
    if set(refs) != {case.repo.replace('/', '__') for case in cases}:
        raise ValueError('冻结仓库来源与案例清单不一致')
    prepare_cases(dashboard.data, cases, refs, progress)
    return True


def prepare_cos(data, slug, cases, source, notify):
    from .case_storage import CaseAssetStorage, mode
    if mode() != 'cos':
        raise ValueError('该案例需要 COS 仓库，请先配置 COS 存储：' + slug)
    notify('source', '使用 COS 固定仓库版本：' + slug + ' / ' + source['sha256'])
    try:
        with tempfile.TemporaryDirectory(prefix='mixed-cos-', dir=data) as temporary:
            CaseAssetStorage(data)._prepare_runtime_repository(
                data, slug, cases, source, Path(temporary), 1, 1,
                lambda stage, message, *_args, **_details: notify(stage, message))
    except RuntimeError as error:
        raise ValueError(f'COS 仓库 {slug} 准备失败：{error}') from None


@contextmanager
def locked(path):
    from runner.locking import acquire, release
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        try:
            acquire(handle)
        except BlockingIOError:
            raise ValueError('仓库正在准备，请稍后重试') from None
        try:
            yield
        finally:
            release(handle)


def registered_sources_ready(row, repositories):
    """Git-backed cases are selectable before their histories are downloaded."""
    refs = row.get('repository_versions')
    if not isinstance(refs, dict) or set(refs) != set(repositories):
        return False
    from .repository_sources import valid_records
    return valid_records(refs)


def remote_only(dashboard, spec, key):
    from .repository_sources import binding, validate_reviewer
    pinned = binding(dashboard, spec, key)
    config = spec if spec is not None else dashboard.config(key)
    if pinned and spec is not None:
        validate_reviewer(config.get('reviewer', config.get('reviewer_id')), pinned['repositories'])
    if config.get('reviewer', config.get('reviewer_id')) != 'server_agent':
        return False
    if spec is not None:
        from reviewers.server_agent.adapter import load_config
        return load_config().get('diff_mode') == 'direct'
    return config.get('diff_mode') == 'direct'
