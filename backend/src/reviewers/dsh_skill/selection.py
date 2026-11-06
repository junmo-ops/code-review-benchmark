"""Bind a complete imported version, never a mutable name or directory reference."""
import hashlib
import json
import shutil
import stat
from types import SimpleNamespace

from runner.paths import PROJECT_ROOT
from runner.runtime import data_root
from runner.storage import save
from .runtime import IsolationError, runtime_mode


def validate_mode(mode, version):
    if not isinstance(mode, str) or mode not in {'baseline', 'skill'}:
        raise ValueError('DSH 须显式选择 Skill 版本或无待测 Skill 基线')
    if (mode == 'baseline' and version is not None) or (mode == 'skill' and not version):
        raise ValueError('Skill 模式与版本不一致')


def identity_digest(record):
    content = json.dumps(record, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()
    return hashlib.sha256(content).hexdigest()


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


def bind(args, cfg, run):
    from webui import skill_assets
    validate_mode(args.skill_mode, args.skill_version)
    cfg['skill_mode'] = args.skill_mode
    if args.skill_mode == 'baseline':
        if args.skill_identity_sha256 is not None or (run / 'skill').exists():
            raise ValueError('基线不能引用 Skill 内容')
        return
    assets = SimpleNamespace(data=data_root(PROJECT_ROOT))
    record = skill_assets.identity(assets, args.skill_version)
    if args.skill_identity_sha256 is not None and identity_digest(record) != args.skill_identity_sha256:
        raise IsolationError('排队后 Skill 冻结身份已改变，停止批次')
    source = skill_assets.version_folder(assets, args.skill_version)
    destination = run / 'skill'
    if destination.exists():
        raise IsolationError('Skill 批次快照已存在，拒绝覆盖')
    shutil.copytree(source / 'content', destination / 'content')
    shutil.copyfile(source / 'package.zip', destination / 'package.zip')
    save(destination / 'manifest.json', record)
    if runtime_mode() == 'pod':
        harden_snapshot(destination)
    skill_assets.verify_copy(destination, record)
    cfg.update(skill_version=record['id'], skill_identity=record)


def verify(cfg, run):
    from webui.skill_assets import verify_copy
    try:
        validate_mode(cfg.get('skill_mode'), cfg.get('skill_version'))
        if cfg['skill_mode'] == 'skill':
            record = cfg['skill_identity']
            if cfg['skill_version'] != record['id']:
                raise ValueError('Skill 身份不一致')
            verify_copy(run / 'skill', record)
        elif cfg.get('skill_identity') is not None or (run / 'skill').exists():
            raise ValueError('基线不能包含待测 Skill 快照')
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise IsolationError('DSH 冻结 Skill 内容校验失败，停止批次') from error


def stage(cfg, run, work):
    verify(cfg, run)
    if cfg['skill_mode'] == 'skill':
        shutil.copytree(run / 'skill', work / 'skill-version')
        if runtime_mode() == 'pod':
            harden_snapshot(work / 'skill-version')
        verify_staged(cfg, work)


def verify_staged(cfg, work):
    from webui.skill_assets import verify_copy
    if cfg['skill_mode'] == 'skill':
        try:
            verify_copy(work / 'skill-version', cfg['skill_identity'])
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
    return dict(status='complete', identity_sha256=identity_digest(cfg['skill_identity']))
