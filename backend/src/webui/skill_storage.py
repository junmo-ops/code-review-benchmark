"""Skill assets on the existing resource COS connection, separate from run backup."""
import hashlib
import json
from pathlib import Path
import tempfile

from .case_storage import CaseAssetStorage, _sha256
from .service import save
from .skill_append import encoded as _encoded

MAX_RECORD_BYTES = 4 * 1024 * 1024


class SkillAssetStorage(CaseAssetStorage):
    @property
    def skill_prefix(self):
        return self.assets_prefix + 'skills/'

    def _key_for(self, identifier, suffix):
        from .skill_assets import IDENTIFIER
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
            raise RuntimeError('Skill COS 身份无效')
        return self.skill_prefix + identifier + '/' + suffix

    def _head(self, key):
        try:
            return self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as error:
            if getattr(error, 'response', {}).get('Error', {}).get('Code') in {'NoSuchKey', '404', 'NotFound'}:
                return None
            raise RuntimeError('无法确认 Skill COS 对象状态') from None

    def store_version(self, folder, record):
        from .skill_append import publish
        from .skill_restore import check_identities, read_entries
        if _sha256(folder / 'package.zip') != record['archive_sha256']:
            raise RuntimeError('Skill 原包在保存期间变化，拒绝发布')
        _, entries = read_entries(self)
        check_identities(entries, record)
        originals = list((self.data / 'skill-assets/archives' / record['id']).glob('*.zip'))
        for path in originals:
            self._verify_original_content(path, record)
        retained = {item['archive']['sha256'] for item in entries if item['id'] == record['id']}
        complete = all(_sha256(path) in retained for path in originals)
        if not complete or not any(item['kind'] == 'version' and item['value']['manifest'] == record for item in entries):
            publish(self, folder / 'package.zip', record, 'version', originals)
        check_identities(read_entries(self)[1], record)

    def store_repacked(self, identifier, path):
        from .skill_append import publish
        from .skill_restore import check_identities, read_entries
        record = json.loads((self.data / 'skill-assets/versions' / identifier / 'manifest.json').read_text())
        self._verify_original_content(path, record)
        _, entries = read_entries(self)
        check_identities(entries, record)
        if not any(item['id'] == identifier and item['kind'] == 'version' for item in entries):
            raise RuntimeError('Skill COS 追加原包前必须先确认主版本')
        digest = _sha256(path)
        if not any(item['id'] == identifier and item['archive']['sha256'] == digest for item in entries):
            publish(self, path, record, 'original')

    def confirm_local(self, record):
        save(self.data / 'skill-assets/confirmed' / (record['id'] + '.json'),
             dict(location=self.location, manifest_sha256=hashlib.sha256(_encoded(record)).hexdigest()))

    def _read_record(self, key):
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        try:
            raw = response['Body'].read(MAX_RECORD_BYTES + 1)
        finally:
            response['Body'].close()
        if (len(raw) > MAX_RECORD_BYTES or response.get('ContentLength') != len(raw)
                or response.get('Metadata', {}).get('sha256') != hashlib.sha256(raw).hexdigest()):
            raise RuntimeError('Skill COS 记录摘要校验失败')
        return json.loads(raw)

    def _keys(self):
        paginator = self.client.get_paginator('list_objects_v2')
        return sorted(item['Key'] for page in paginator.paginate(Bucket=self.bucket, Prefix=self.skill_prefix)
                      for item in page.get('Contents', []))

    def restore(self, dashboard):
        from .skill_restore import restore_all
        try:
            count = restore_all(self, dashboard)
            self._verify_references(dashboard)
            return count
        except Exception as error:
            raise RuntimeError('Skill 资产恢复失败，服务未启动：' + str(error)) from error

    def _verify_references(self, dashboard):
        from .skill_assets import identity
        from .queue import Queue
        references = [json.loads(path.read_text()) for path in
                      (self.data / 'runs').glob('*/dsh-frozen-config.json')]
        queue = self.data / 'ui-state/queue.sqlite3'
        if queue.is_file():
            references.extend(Queue(queue).skill_references())
        for reference in references:
            if reference.get('skill_mode') != 'skill':
                continue
            if identity(dashboard, reference['skill_version']) != reference['skill_identity']:
                raise RuntimeError('Skill 恢复内容与历史批次或排队身份冲突')

    def _download_archive(self, identifier, record, target, key=None):
        from .skill_package import limits
        from .skill_assets import IDENTIFIER
        digest, size = record['sha256'], record['size']
        if (not isinstance(digest, str) or not IDENTIFIER.fullmatch(digest)
                or type(size) is not int or not 0 < size <= limits()['upload_max_bytes']):
            raise RuntimeError('Skill COS 原包记录无效')
        if target.exists() and (target.is_symlink() or not target.is_file()
                                or target.stat().st_size != size or _sha256(target) != digest):
            raise RuntimeError('Skill 本地不可变原包冲突，拒绝覆盖')
        if key is None:
            self._download_object(self._key_for(identifier, 'archives/' + digest + '.zip'), target, size, digest)
        elif not target.exists():
            from .skill_append import read_verified
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.part')
            try:
                with temporary.open('xb') as output:
                    read_verified(self, dict(key=key, size=size, sha256=digest), output=output)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)

    def _restore_version(self, dashboard, identifier, value, archive_key=None):
        from .skill_assets import _verify, _version_path
        from .skill_package import unpack
        record, archive = value['manifest'], value['archive']
        if value['format'] != 'skill-assets-v1' or record['archive_sha256'] != archive['sha256']:
            raise RuntimeError('Skill COS 版本记录无效')
        destination = _version_path(dashboard, identifier)
        root = self.data / 'skill-assets'
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.restore-', dir=root) as temporary:
            stage = Path(temporary)
            self._download_archive(identifier, archive, stage / 'package.zip', archive_key)
            (stage / 'content').mkdir()
            extracted = unpack(stage / 'package.zip', stage / 'content', lambda *args: None)
            if any(record[key] != val for key, val in extracted.items()):
                raise RuntimeError('Skill 原包与版本记录内容冲突')
            save(stage / 'manifest.json', record)
            _verify(stage, record, identifier)
            if destination.exists() or destination.is_symlink():
                if destination.is_symlink() or json.loads((destination / 'manifest.json').read_text()) != record:
                    raise RuntimeError('Skill 本地不可变版本冲突，拒绝覆盖')
                _verify(destination, record, identifier)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                stage.replace(destination)

    def _restore_originals(self, identifier, record, keys, additional=()):
        prefix = self._key_for(identifier, 'originals/')
        available = {record['archive_sha256'], *additional}
        for key in keys:
            if not key.startswith(prefix) or not key.endswith('.json'):
                continue
            archive = self._read_record(key)
            if key != prefix + archive['sha256'] + '.json':
                raise RuntimeError('Skill COS 原包引用无效')
            target = self.data / 'skill-assets/archives' / identifier / (archive['sha256'] + '.zip')
            self._download_archive(identifier, archive, target)
            self._verify_original_content(target, record)
            available.add(archive['sha256'])
        for path in (self.data / 'skill-assets/archives' / identifier).glob('*.zip'):
            if path.stem not in available:
                raise RuntimeError('Skill COS 重复导入原包记录缺失')

    def _verify_original_content(self, archive, record):
        from .skill_package import unpack
        with tempfile.TemporaryDirectory(prefix='.original-', dir=self.data / 'skill-assets') as folder:
            extracted = unpack(archive, Path(folder), lambda *args: None)
            if any(record[key] != val for key, val in extracted.items()):
                raise RuntimeError('Skill 重复导入原包与不可变内容身份冲突')


def confirmed(data, record):
    from .case_storage import _settings
    _, options = _settings()
    location = '|'.join(str(options[key]).strip().strip('/') if key == 'prefix'
                        else str(options[key]).strip() for key in ('endpoint', 'region', 'bucket', 'prefix'))
    receipt = json.loads((Path(data) / 'skill-assets/confirmed' / (record['id'] + '.json')).read_text())
    if receipt != dict(location=location, manifest_sha256=hashlib.sha256(_encoded(record)).hexdigest()):
        raise ValueError('Skill COS 保存尚未确认或存储位置已变化')
