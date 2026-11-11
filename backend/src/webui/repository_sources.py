"""Case-package source declarations and batch-pinned repository bindings."""
import copy
import json
import re


def declarations(cases, manifest, data=None):
    from .automatic_repositories import sources
    defaults = sources(cases)
    if 'repository_sources' not in manifest:
        return defaults
    refs = manifest['repository_sources']
    if not isinstance(refs, dict) or set(refs) != set(defaults):
        raise ValueError('repository_sources 必须为案例清单中的每个仓库指定来源')
    return {slug: resolve_declaration(slug, item, defaults[slug], data) for slug, item in refs.items()}


def resolve_declaration(slug, item, default, data):
    if isinstance(item, str):
        item = {'source': item}
    if not isinstance(item, dict):
        raise ValueError('仓库来源须为 git 或 cos：' + slug)
    if item.get('source') == 'git':
        return normalize(slug, dict(default, **item))
    if item.get('source') != 'cos' or 'sha256' in item or 'size' in item:
        return normalize(slug, item)
    alias = item.get('repository', slug)
    if not valid_repository_alias({'repository': alias}):
        raise ValueError('COS 仓库标识无效：' + slug)
    if item.get('storage', 'repository-pool') != 'repository-pool':
        raise ValueError('自动选择 COS 版本仅支持已登记仓库资源：' + slug)
    if data is None:
        return {'source': 'cos', 'repository': alias}
    from .case_assets import repository_registry, _current_repository_record
    current = _current_repository_record(repository_registry(data)['repositories'], alias)
    return normalize(slug, dict(item, repository=alias, sha256=current['sha256'], size=current['size']))


def normalize(slug, item):
    from .automatic_repositories import valid
    if not isinstance(item, dict):
        raise ValueError('仓库来源配置必须为对象：' + slug)
    if item.get('source') == 'git' and valid(slug, item):
        return {'source': 'git', 'url': item['url']}
    if item.get('source') != 'cos':
        raise ValueError('仓库来源须为 git 或 cos，Git 地址须与案例仓库名一致：' + slug)
    record = {'source': item.get('storage', 'repository-pool'),
              'sha256': item.get('sha256'), 'size': item.get('size'),
              'repository': item.get('repository', slug)}
    if not valid_records({slug: record}):
        raise ValueError('COS 仓库须指定有效的仓库标识、bundle SHA256、大小和存储类型：' + slug)
    return record


def valid_records(refs):
    from .case_storage import CaseAssetStorage
    from .case_upload import limits
    return CaseAssetStorage._valid_repository_records(refs, limits()['unpacked_max_bytes'])


def valid_repository_alias(item):
    alias = item.get('repository')
    return alias is None or (isinstance(alias, str) and re.fullmatch(r'[A-Za-z0-9_.-]+', alias) is not None)


def pin_creation(dashboard, spec):
    from .case_assets import _registry
    row = next((row for row in _registry(dashboard.data)['datasets']
                if row.get('id') == spec['dataset']), None)
    if not row or not row.get('repository_versions'):
        return
    refs = row['repository_versions']
    validate_reviewer(spec.get('reviewer'), refs)
    spec['repository_binding'] = {'dataset_sha256': row['dataset_sha256'],
                                  'repositories': copy.deepcopy(refs)}


def validate_reviewer(reviewer, refs):
    if reviewer != 'server_agent':
        return
    unavailable = [slug for slug, item in refs.items() if item.get('source') != 'git']
    if unavailable:
        raise ValueError('服务端 Agent 自行拉取 Git，以下仓库仍为 COS 来源，请先迁移并更新案例集：'
                         + '、'.join(unavailable))


def binding(dashboard, spec, key):
    from .service import save
    path = dashboard.run_dir(key) / 'repository-sources.json'
    if path.is_file():
        value = json.loads(path.read_text(encoding='utf-8'))
        if spec is not None and spec.get('repository_binding') != value:
            raise ValueError('批次仓库来源与已冻结配置不一致')
        return value
    if spec is not None and 'repository_binding' in spec:
        value = spec['repository_binding']
        save(path, value)
        return value
    return None


def update_binding(dashboard, existing, references):
    if not existing or existing.get('repository_versions') == references:
        return
    from .resource_dependencies import ensure_resource_unused
    ensure_resource_unused(dashboard, dataset_sha256=existing['dataset_sha256'])
    preserve_history(dashboard, existing)
    existing['repository_versions'] = copy.deepcopy(references)


def preserve_history(dashboard, row):
    from .service import save
    previous = row.get('repository_versions')
    if not previous:
        raise ValueError('旧案例集缺少固定仓库来源，不能覆盖；请先恢复其资源记录')
    for path in (dashboard.data / 'runs').glob('*/run-config.json'):
        config = json.loads(path.read_text(encoding='utf-8'))
        suite = config.get('suite', {})
        suite = suite if isinstance(suite, str) else suite.get('id')
        if suite != row['suite'] or config.get('dataset_sha256') != row['dataset_sha256']:
            continue
        target = path.parent / 'repository-sources.json'
        if not target.exists():
            save(target, {'dataset_sha256': row['dataset_sha256'], 'repositories': previous})


def valid_record(slug, item, maximum):
    from .automatic_repositories import valid
    if not isinstance(item, dict) or not valid_repository_alias(item):
        return False
    if item.get('source') == 'git':
        return valid(slug, item)
    return valid_bundle(slug, item, maximum)


def valid_bundle(slug, item, maximum):
    return (isinstance(slug, str) and re.fullmatch(r'[A-Za-z0-9_.-]+', slug) is not None
            and isinstance(item.get('sha256'), str)
            and re.fullmatch(r'[a-f0-9]{64}', item['sha256']) is not None
            and type(item.get('size')) is int and 0 < item['size'] <= maximum
            and item.get('source', 'case-archive') in {'case-archive', 'repository-pool'})


def summary(row):
    refs = row.get('repository_versions', {})
    if not refs:
        return '历史资源'
    git_count = sum(item.get('source') == 'git' for item in refs.values())
    cos_count = len(refs) - git_count
    return ' / '.join(label for label in (f'Git {git_count} 个' if git_count else '',
                                          f'COS {cos_count} 个' if cos_count else '') if label)
