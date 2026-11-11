"""Bind complete imported versions as one frozen subject, never a mutable name or directory reference.

A batch selects either an explicit baseline or one or more complete imported
Skill versions. ``skill_mode`` stays ``'baseline' | 'skill'``: a selection of
N>=2 immutable versions is a Skill 组合, N=1 is exactly the existing single
Skill. New batches freeze the member-list structure (``skill_identities`` /
``skill_versions`` / ``skill_combination_digest``); legacy single-valued
``skill_identity`` / ``skill_version`` configs are read as a one-member
combination and keep the old ``run/skill`` snapshot layout.
"""
import hashlib
import json
import shutil
import stat
from types import SimpleNamespace

from runner.paths import PROJECT_ROOT
from runner.runtime import data_root
from runner.storage import save
from .runtime import IsolationError, runtime_mode


def validate_mode(mode, version=None, versions=None):
    """Mode/members shape check only; per-version and combination checks happen
    where the identity records are available (validate_members / verify)."""
    if not isinstance(mode, str) or mode not in {'baseline', 'skill'}:
        raise ValueError('DSH 须显式选择 Skill 版本或无待测 Skill 基线')
    if mode == 'baseline':
        if version is not None or versions:
            raise ValueError('Skill 模式与版本不一致')
    elif not version and not versions:
        raise ValueError('Skill 模式与版本不一致')


def identity_digest(record):
    content = json.dumps(record, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()
    return hashlib.sha256(content).hexdigest()


def combination_digest(records):
    """组合摘要 = canonical sha256 over the sorted member identity digests.

    The member set is unordered: order never participates in the identity.
    A single member collapses to its own identity digest, so N=1 is
    observably identical to the legacy single-Skill identity.
    """
    if len(records) == 1:
        return identity_digest(records[0])
    digests = sorted(identity_digest(record) for record in records)
    content = json.dumps(digests, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()
    return hashlib.sha256(content).hexdigest()


def validate_members(versions, records):
    """Combination-level checks over chosen members.

    ``versions`` are the selected immutable ids and ``records`` their complete
    identities (already verified as imported and content-complete by
    skill_assets.identity). Enforces per-member identity consistency plus the
    combination rules: no same-name members and no duplicate content identity.
    """
    if isinstance(versions, str):
        versions = [versions]
    if (not isinstance(versions, list) or not isinstance(records, list)
            or not versions or len(versions) != len(records)):
        raise ValueError('Skill 成员列表无效')
    names, contents = set(), set()
    for version, record in zip(versions, records):
        if not isinstance(version, str) or not isinstance(record, dict):
            raise ValueError('Skill 成员列表无效')
        if record.get('id') != version or record.get('content_sha256') != version:
            raise ValueError('Skill 身份不一致')
        if version in contents:
            raise ValueError('组合内不允许重复内容身份：' + version)
        contents.add(version)
        name = record.get('name')
        if not isinstance(name, str) or not name:
            raise ValueError('Skill 成员身份缺名称')
        if name in names:
            raise ValueError('组合内不允许同名成员：' + name)
        names.add(name)


def members(cfg):
    """Normalize the frozen identity carrier to [(version, record)] pairs.

    New batches store the member lists; legacy batches store the single-valued
    ``skill_identity`` / ``skill_version`` and are treated as a one-member
    combination. When both representations are present (new N=1 batches write
    both for compatibility) they must agree.
    """
    versions = cfg.get('skill_versions')
    records = cfg.get('skill_identities')
    if versions is not None or records is not None:
        return _listed_members(cfg, versions, records)
    return _legacy_member(cfg)


def _listed_members(cfg, versions, records):
    if (not isinstance(versions, list) or not isinstance(records, list)
            or not versions or len(versions) != len(records)):
        raise ValueError('Skill 成员列表无效')
    items = []
    for version, record in zip(versions, records):
        if not isinstance(version, str) or not isinstance(record, dict):
            raise ValueError('Skill 成员列表无效')
        if record.get('id') != version:
            raise ValueError('Skill 身份不一致')
        items.append((version, record))
    _redundant_projection(cfg, items)
    return items


def _redundant_projection(cfg, items):
    if len(items) != 1 or (cfg.get('skill_version') is None and cfg.get('skill_identity') is None):
        return
    if cfg.get('skill_version') != items[0][0] or cfg.get('skill_identity') != items[0][1]:
        raise ValueError('Skill 身份不一致')


def _legacy_member(cfg):
    record = cfg.get('skill_identity')
    if not isinstance(record, dict):
        raise ValueError('Skill 身份不一致')
    version = cfg.get('skill_version')
    if not isinstance(version, str):
        version = record.get('id')
    if not isinstance(version, str) or record.get('id') != version:
        raise ValueError('Skill 身份不一致')
    return [(version, record)]


def _frozen_combination(cfg):
    """Frozen combination validation; returns (items, digest).

    Covers every combination-level rule on the frozen config: per-member id
    consistency, unique author names, unique content identities, and the
    canonical combination digest against ``skill_combination_digest`` when the
    batch was frozen with one (legacy batches have none).
    """
    items = members(cfg)
    validate_members([version for version, _ in items], [record for _, record in items])
    digest = combination_digest([record for _, record in items])
    frozen = cfg.get('skill_combination_digest')
    if frozen is not None and frozen != digest:
        raise ValueError('Skill 组合摘要不一致')
    return items, digest


def _any_snapshot_layout(run):
    return (run / 'skill').exists() or (run / 'skills').exists()


def uses_new_layout(cfg):
    """The member-list structure is present: new snapshot layout
    ``run/skills/<content_sha256>`` / ``work/skills/<content_sha256>`` instead
    of the legacy ``run/skill`` / ``work/skill-version`` single-Skill layout."""
    return 'skill_versions' in cfg or 'skill_identities' in cfg


def _snapshot_folder(run, cfg, record):
    if uses_new_layout(cfg):
        return run / 'skills' / record['content_sha256']
    return run / 'skill'


def _staged_folder(work, cfg, record):
    if uses_new_layout(cfg):
        return work / 'skills' / record['content_sha256']
    return work / 'skill-version'


def harden_snapshot(folder):
    """Make a frozen Skill snapshot physically un-writable by the same user.

    Only meaningful in POD mode: it shares the host filesystem with the DSH
    process (ADR-0002) and bash is not path-scoped by the software guard, so a
    Skill script can otherwise write into its own frozen directory (e.g.
    Python bytecode caches like ``scripts/__pycache__/*.pyc``), which later
    fails ``verify_staged`` and stops the batch after a completed review.
    Removing write bits makes any such write fail with EACCES instead; Python
    silently skips ``__pycache__``, and legitimate script outputs (repo, work
    dir) are untouched.
    """
    for path in (folder, *folder.rglob('*')):
        if path.is_symlink():
            raise IsolationError('Skill 快照不能包含链接，无法加固')
        mode = (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
                | (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH if path.is_dir() else 0))
        path.chmod(mode)


def verify_frozen(cfg, run):
    try:
        original = json.loads((run / 'dsh-frozen-config.json').read_text())
        if original != cfg:
            raise ValueError('initial batch configuration changed')
    except (OSError, ValueError) as error:
        raise IsolationError('DSH 初始冻结条件已变化或不可核验，停止批次') from error


def _arguments_versions(args):
    value = getattr(args, 'skill_versions', None) or getattr(args, 'skill_version', None)
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return list(value)


def bind(args, cfg, run):
    versions = _arguments_versions(args)
    validate_mode(args.skill_mode, versions[0] if versions else None, versions)
    cfg['skill_mode'] = args.skill_mode
    if args.skill_mode == 'baseline':
        _bind_baseline(args, run)
        return
    _bind_members(args, cfg, run, versions)


def _bind_baseline(args, run):
    if (getattr(args, 'skill_identity_sha256', None) is not None
            or getattr(args, 'skill_combination_sha256', None) is not None
            or _any_snapshot_layout(run)):
        raise ValueError('基线不能引用 Skill 内容')


def _bind_members(args, cfg, run, versions):
    from webui import skill_assets
    assets = SimpleNamespace(data=data_root(PROJECT_ROOT))
    records = [skill_assets.identity(assets, version) for version in versions]
    validate_members(versions, records)
    digest = combination_digest(records)
    _verify_queued_digests(args, records, digest)
    for version, record in zip(versions, records):
        _copy_member(run, assets, version, record)
    cfg.update(skill_versions=versions, skill_identities=records, skill_combination_digest=digest)
    if len(records) == 1:
        cfg.update(skill_version=versions[0], skill_identity=records[0])


def _verify_queued_digests(args, records, digest):
    frozen = getattr(args, 'skill_combination_sha256', None)
    if frozen is not None and digest != frozen:
        raise IsolationError('排队后 Skill 组合摘要已改变，停止批次')
    legacy = getattr(args, 'skill_identity_sha256', None)
    if legacy is not None and (len(records) != 1 or identity_digest(records[0]) != legacy):
        raise IsolationError('排队后 Skill 冻结身份已改变，停止批次')


def _copy_member(run, assets, version, record):
    from webui import skill_assets
    destination = run / 'skills' / record['content_sha256']
    if destination.exists():
        raise IsolationError('Skill 批次快照已存在，拒绝覆盖')
    source = skill_assets.version_folder(assets, version)
    shutil.copytree(source / 'content', destination / 'content')
    shutil.copyfile(source / 'package.zip', destination / 'package.zip')
    save(destination / 'manifest.json', record)
    if runtime_mode() == 'pod':
        harden_snapshot(destination)
    skill_assets.verify_copy(destination, record)


def verify(cfg, run):
    from webui.skill_assets import verify_copy
    try:
        validate_mode(cfg.get('skill_mode'), cfg.get('skill_version'), cfg.get('skill_versions'))
        if cfg['skill_mode'] == 'skill':
            for version, record in _frozen_combination(cfg)[0]:
                if version != record['id']:
                    raise ValueError('Skill 身份不一致')
                verify_copy(_snapshot_folder(run, cfg, record), record)
        elif (cfg.get('skill_identity') is not None or cfg.get('skill_versions') is not None
                or cfg.get('skill_identities') is not None or _any_snapshot_layout(run)):
            raise ValueError('基线不能包含待测 Skill 快照')
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise IsolationError('DSH 冻结 Skill 内容校验失败，停止批次') from error


def stage(cfg, run, work):
    verify(cfg, run)
    if cfg['skill_mode'] == 'skill':
        for version, record in _frozen_combination(cfg)[0]:
            if version != record['id']:
                raise IsolationError('DSH 冻结 Skill 身份不一致，停止批次')
            target = _staged_folder(work, cfg, record)
            shutil.copytree(_snapshot_folder(run, cfg, record), target)
            if runtime_mode() == 'pod':
                harden_snapshot(target)
        verify_staged(cfg, work)


def verify_staged(cfg, work):
    from webui.skill_assets import verify_copy
    if cfg['skill_mode'] == 'skill':
        try:
            for version, record in _frozen_combination(cfg)[0]:
                if version != record['id']:
                    raise ValueError('Skill 身份不一致')
                verify_copy(_staged_folder(work, cfg, record), record)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise IsolationError('DSH 案例 Skill 快照内容不一致，停止批次') from error


def verify_after_execution(cfg, work):
    """Post-run staged-snapshot check.

    POD mode shares the host filesystem with untrusted execution (ADR-0002)
    and cannot guarantee OS-level write prevention, so a snapshot mutation
    after a run is a capability violation of that case: the loading proof is
    void, the case must not publish a quality answer, but the batch continues.
    Any other mode keeps the original fail-closed semantics: a mutation there
    means the isolation boundary itself was broken.
    """
    try:
        verify_staged(cfg, work)
    except IsolationError as error:
        if cfg.get('dsh_policy', {}).get('runtime_mode') != 'pod' or cfg.get('skill_mode') != 'skill':
            raise
        raise ValueError('DSH 案例执行期间冻结 Skill 快照被改动，该案例不发布质量答卷；批次继续') from error


def integrity(cfg):
    _, digest = _frozen_combination(cfg)
    return dict(status='complete', identity_sha256=digest)
