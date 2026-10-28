"""Read-only legacy and append-only Skill restoration with frozen identity checks."""
import hashlib
import json

from . import skill_append


def _identity(record):
    from .skill_assets import IDENTITY_FIELDS
    return {key: record[key] for key in IDENTITY_FIELDS}


def check_identities(entries, record):
    expected = _identity(record)
    for item in entries:
        if item['id'] == record['id'] and _identity(item['value']['manifest']) != expected:
            raise RuntimeError('Skill COS 同一内容身份出现冲突的冻结记录；禁止自动选择或覆盖')


def _legacy(storage, keys):
    entries = []
    for key in keys:
        if not key.endswith('/commit.json'):
            continue
        identifier = key[len(storage.skill_prefix):].split('/')[0]
        if key != storage._key_for(identifier, 'commit.json'):
            raise RuntimeError('Skill COS 旧版发布标记路径无效')
        record_key = storage._key_for(identifier, 'record.json')
        if record_key not in keys:
            raise RuntimeError('Skill COS 已确认版本记录缺失')
        value, commit = storage._read_record(record_key), storage._read_record(key)
        digest = hashlib.sha256(skill_append.encoded(value)).hexdigest()
        if commit != dict(format='skill-commit-v1', id=identifier, record_sha256=digest):
            raise RuntimeError('Skill COS 旧版发布标记与记录不一致')
        archive = value['archive']
        ref = dict(archive, key=storage._key_for(identifier, 'archives/' + archive['sha256'] + '.zip'))
        skill_append.check_head(storage, ref)
        entries.append(dict(id=identifier, kind='version', value=value, archive=ref))
    return entries


def _groups(entries):
    result = {}
    for item in entries:
        identifier = item['id']
        record = item['value']['manifest']
        if record['id'] != identifier:
            raise RuntimeError('Skill COS 发布与版本身份不一致')
        group = result.setdefault(identifier, dict(version=[], original=[]))
        group[item['kind']].append(item)
    for group in result.values():
        if not group['version']:
            raise RuntimeError('Skill COS 已发布原包的主版本记录缺失')
        check_identities(group['version'] + group['original'], group['version'][0]['value']['manifest'])
    return result


def _choose(storage, identifier, versions):
    local = storage.data / 'skill-assets/versions' / identifier / 'manifest.json'
    if local.exists():
        record = json.loads(local.read_text())
        candidates = [item for item in versions if item['value']['manifest'] == record]
        if not candidates:
            raise RuntimeError('Skill 本地不可变版本冲突，或已确认发布记录丢失')
        return candidates[0]
    return min(versions, key=lambda item: skill_append.encoded(item['value']['manifest']))


def _restore_originals(storage, identifier, record, originals):
    for item in originals:
        archive = item['archive']
        if archive['sha256'] == record['archive_sha256']:
            continue
        target = storage.data / 'skill-assets/archives' / identifier / (archive['sha256'] + '.zip')
        storage._download_archive(identifier, archive, target, archive['key'])
        storage._verify_original_content(target, record)


def read_entries(storage):
    keys = [key for key in storage._keys() if not key.startswith(skill_append.prefix(storage))]
    entries = _legacy(storage, keys) + skill_append.catalog(storage)
    return keys, entries


def restore_all(storage, dashboard):
    keys, entries = read_entries(storage)
    groups = _groups(entries)
    for receipt in (storage.data / 'skill-assets/confirmed').glob('*.json'):
        if receipt.stem not in groups:
            raise RuntimeError('Skill COS 版本记录缺失，停止恢复')
    for identifier, group in groups.items():
        chosen = _choose(storage, identifier, group['version'])
        record = chosen['value']['manifest']
        storage._restore_version(dashboard, identifier, chosen['value'], chosen['archive']['key'])
        additional = [item['archive']['sha256'] for item in group['original']]
        storage._restore_originals(identifier, record, keys, additional)
        _restore_originals(storage, identifier, record, group['original'])
        storage.confirm_local(record)
    return len(groups)
