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
            destination = data / 'repos' / slug
            if ready(destination, items, _validate_repository_cases):
                notify('ready', '复用已校验的案例提交：' + slug)
                continue
            source = references[slug]
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
    if not row or suite not in {'aacr', 'swe_prbench_llm', 'codefuse_cr'}:
        return False
    refs = row.get('repository_versions', {})
    if not refs or not all(value.get('source') == 'git' for value in refs.values()):
        return False  # Legacy datasets keep their immutable bundle bindings.
    dataset = dashboard.dataset_path(spec['dataset']) if spec else dashboard.run_dir(key) / 'dataset.jsonl'
    from .case_storage import _sha256
    if _sha256(dataset) != digest:
        raise ValueError('冻结案例清单校验失败；禁止按其他案例恢复仓库')
    cases = checked_instances(dataset, suite)
    prepare_cases(dashboard.data, cases, refs, progress)
    return True


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
    return all(isinstance(item, dict) and item.get('source') == 'git' and valid(slug, item)
               for slug, item in refs.items())


def remote_only(dashboard, spec, key):
    config = spec if spec is not None else dashboard.config(key)
    if config.get('reviewer', config.get('reviewer_id')) != 'server_agent':
        return False
    if spec is not None:
        from reviewers.server_agent.adapter import load_config
        return load_config().get('diff_mode') == 'direct'
    return config.get('diff_mode') == 'direct'
