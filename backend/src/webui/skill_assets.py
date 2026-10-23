"""Immutable local Skill versions, published only after full package validation."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

from .case_storage import mode
from .service import now, save
from .skill_package import content_digest, limits, unpack, verify_metadata

IDENTIFIER = re.compile(r'^[a-f0-9]{64}$')
IDENTITY_FIELDS = ('id', 'name', 'description', 'main', 'files', 'content_sha256', 'archive_sha256')


def identity(dashboard, identifier):
    record = detail(dashboard, identifier)
    return {key: record[key] for key in IDENTITY_FIELDS}


def version_folder(dashboard, identifier):
    detail(dashboard, identifier)
    return _version_path(dashboard, identifier)


def verify_copy(folder, record):
    if folder.is_symlink() or json.loads((folder / 'manifest.json').read_text()) != record:
        raise ValueError('Skill 快照记录与冻结身份不一致')
    _verify(folder, record, record['id'])


def _root(dashboard):
    return dashboard.data / 'skill-assets'


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _version_path(dashboard, identifier):
    if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
        raise ValueError('Skill 版本标识无效')
    root = _root(dashboard).resolve()
    target = root / 'versions' / identifier
    if target.resolve() != target:
        raise ValueError('Skill 版本路径无效')
    return target


def detail(dashboard, identifier):
    folder = _version_path(dashboard, identifier)
    try:
        record = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        _verify(folder, record, identifier)
        if mode() == 'cos':
            from .skill_storage import confirmed
            confirmed(dashboard.data, record)
        record['archives'] = _archives(dashboard, record)
        return record
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ValueError('Skill 版本不存在或内容校验失败：' + identifier) from error


def _archives(dashboard, record):
    result = [record['archive_sha256']]
    for path in sorted((_root(dashboard) / 'archives' / record['id']).glob('*.zip')):
        digest = path.stem
        if not IDENTIFIER.fullmatch(digest) or path.is_symlink() or _sha256(path) != digest:
            raise ValueError('Skill 原包记录不一致')
        result.append(digest)
    return result


def _retain_repacked(dashboard, record, staging):
    existing = detail(dashboard, record['id'])
    if record['archive_sha256'] in existing['archives']:
        return
    folder = _root(dashboard) / 'archives' / record['id']
    folder.mkdir(parents=True, exist_ok=True)
    (staging / 'package.zip').replace(folder / (record['archive_sha256'] + '.zip'))


def _verify(folder, record, identifier):
    if (folder / 'manifest.json').is_symlink():
        raise ValueError('Skill 版本记录不能为链接')
    if record['id'] != identifier or record['content_sha256'] != content_digest(record['files']):
        raise ValueError('Skill 内容身份不一致')
    if record['content_sha256'] != identifier or not IDENTIFIER.fullmatch(record['archive_sha256']):
        raise ValueError('Skill 版本身份不一致')
    content = folder / 'content'
    expected = {item['path'] for item in record['files']}
    actual = {path.relative_to(content).as_posix() for path in content.rglob('*') if path.is_file()}
    if actual != expected or record['main'] not in expected:
        raise ValueError('Skill 文件清单不一致')
    for item in record['files']:
        _verify_file(content, item)
    verify_metadata(content, record)
    archive = folder / 'package.zip'
    if archive.is_symlink() or _sha256(archive) != record['archive_sha256']:
        raise ValueError('Skill 原包内容不一致')


def _verify_file(content, item):
    path = content / item['path']
    if (not path.resolve().is_relative_to(content.resolve()) or path.is_symlink()
            or path.stat().st_size != item['size'] or _sha256(path) != item['sha256']):
        raise ValueError('Skill 文件内容不一致')
    if any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(content)):
        raise ValueError('Skill 内容目录不能为链接')


def listing(dashboard):
    result = dict(supported=True, storage_mode=mode(), versions=[], unavailable=[], limits=limits())
    for folder in sorted((_root(dashboard) / 'versions').glob('*')):
        if not IDENTIFIER.fullmatch(folder.name):
            continue
        try:
            result['versions'].append(detail(dashboard, folder.name))
        except ValueError as error:
            result['unavailable'].append(dict(id=folder.name, message=str(error)))
    return result


def install(dashboard, archive, progress):
    storage = None
    if mode() == 'cos':
        from .skill_storage import SkillAssetStorage
        storage = SkillAssetStorage(dashboard.data)
        storage.restore(dashboard)
    root = _root(dashboard)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.import-', dir=root) as temporary:
        staging = Path(temporary) / 'version'
        content = staging / 'content'
        content.mkdir(parents=True)
        record = unpack(archive, content, progress)
        identifier = record['content_sha256']
        record.update(id=identifier, archive_sha256=_sha256(archive), created_at=now())
        shutil.copyfile(archive, staging / 'package.zip')
        save(staging / 'manifest.json', record)
        _verify(staging, record, identifier)
        destination = _version_path(dashboard, identifier)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            existing = json.loads((destination / 'manifest.json').read_text())
            _verify(destination, existing, identifier)
            if storage:
                for digest in _archives(dashboard, existing)[1:]:
                    path = _root(dashboard) / 'archives' / identifier / (digest + '.zip')
                    storage.store_repacked(identifier, path)
                storage.store_version(destination, existing)
                if record['archive_sha256'] != existing['archive_sha256']:
                    storage.store_repacked(identifier, staging / 'package.zip')
                storage.confirm_local(existing)
            _retain_repacked(dashboard, record, staging)
        else:
            if storage:
                storage.store_version(staging, record)
            staging.replace(destination)
            if storage:
                storage.confirm_local(record)
    message = 'COS 对象大小及摘要元数据已确认保存' if storage else '本地保存'
    return dict(message='Skill 版本已导入（' + message + '）', skill_version=identifier)


def response(dashboard, tail='', query=None):
    parts = tail.split('/')
    if not tail:
        return listing(dashboard), 'application/json', None
    record = detail(dashboard, parts[0])
    if len(parts) == 1:
        return record, 'application/json', None
    folder = _version_path(dashboard, parts[0])
    if parts[1:] == ['archive']:
        digest = (query or {}).get('sha256', record['archive_sha256'])
        if digest not in record['archives']:
            raise ValueError('Skill 原包引用无效')
        path = folder / 'package.zip' if digest == record['archive_sha256'] else (
            _root(dashboard) / 'archives' / record['id'] / (digest + '.zip'))
        return path.read_bytes(), 'application/zip', digest + '.zip'
    if parts[1:] == ['file']:
        name = (query or {}).get('path')
        if name not in {item['path'] for item in record['files']}:
            raise ValueError('Skill 资源引用无效')
        return (folder / 'content' / name).read_bytes(), 'application/octet-stream', 'resource.bin'
    raise ValueError('Skill 资源路径无效')
