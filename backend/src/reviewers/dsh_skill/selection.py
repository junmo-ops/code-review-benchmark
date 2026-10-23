"""Bind a complete imported version, never a mutable name or directory reference."""
import hashlib
import json
import shutil
from types import SimpleNamespace

from runner.paths import PROJECT_ROOT
from runner.runtime import data_root
from runner.storage import save
from .runtime import IsolationError


def validate_mode(mode, version):
    if not isinstance(mode, str) or mode not in {'baseline', 'skill'}:
        raise ValueError('DSH 须显式选择 Skill 版本或无待测 Skill 基线')
    if (mode == 'baseline' and version is not None) or (mode == 'skill' and not version):
        raise ValueError('Skill 模式与版本不一致')


def identity_digest(record):
    content = json.dumps(record, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()
    return hashlib.sha256(content).hexdigest()


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
        verify_staged(cfg, work)


def verify_staged(cfg, work):
    from webui.skill_assets import verify_copy
    if cfg['skill_mode'] == 'skill':
        try:
            verify_copy(work / 'skill-version', cfg['skill_identity'])
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise IsolationError('DSH 案例 Skill 快照内容不一致，停止批次') from error


def integrity(cfg):
    return dict(status='complete', identity_sha256=identity_digest(cfg['skill_identity']))
