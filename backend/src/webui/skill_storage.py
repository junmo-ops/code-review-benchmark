"""Skill assets on the existing resource COS connection, separate from run backup."""
import hashlib
import io
import json
from pathlib import Path
import tempfile

from .case_storage import CaseAssetStorage, _sha256
from .service import save

MAX_RECORD_BYTES = 4 * 1024 * 1024


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')


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

    def _immutable(self, key, stream, size, digest, content_type):
        self._require_conditional_writes()
        head = self._head(key)
        if head is not None:
            if head.get('ContentLength') != size or head.get('Metadata', {}).get('sha256') != digest:
                raise RuntimeError('Skill COS 不可变内容冲突，拒绝覆盖')
            return
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=stream, ContentLength=size,
                                   ContentType=content_type, Metadata={'sha256': digest}, IfNoneMatch='*')
        except Exception:
            # A lost response or a concurrent identical import can still be confirmed.
            pass
        # Confirmation is HEAD size/digest metadata, not a full downloaded readback.
        head = self._head(key)
        if head is None or head.get('ContentLength') != size or head.get('Metadata', {}).get('sha256') != digest:
            raise RuntimeError('Skill 资源未能可靠保存到 COS，或不可变内容冲突；版本不可选，请重传同一 ZIP')

    def _require_conditional_writes(self):
        if not getattr(self, '_conditional_writes_checked', False):
            from .conditional_storage import require_atomic_create
            require_atomic_create(self.client, self.bucket, self.skill_prefix)
            self._conditional_writes_checked = True

    def _archive(self, identifier, path):
        digest = _sha256(path)
        key = self._key_for(identifier, 'archives/' + digest + '.zip')
        with path.open('rb') as stream:
            self._immutable(key, stream, path.stat().st_size, digest, 'application/zip')
        return dict(sha256=digest, size=path.stat().st_size)

    def store_version(self, folder, record):
        archive = dict(sha256=record['archive_sha256'], size=(folder / 'package.zip').stat().st_size)
        value = dict(format='skill-assets-v1', manifest=record, archive=archive)
        raw = _encoded(value)
        if len(raw) > MAX_RECORD_BYTES:
            raise RuntimeError('Skill COS 版本记录超过恢复上限 4 MiB；请缩短包内路径或减少文件数')
        if self._archive(record['id'], folder / 'package.zip') != archive:
            raise RuntimeError('Skill 原包在保存期间变化，拒绝发布')
        self._immutable(self._key_for(record['id'], 'record.json'), io.BytesIO(raw), len(raw),
                        hashlib.sha256(raw).hexdigest(), 'application/json')
        self._commit_record(record['id'], value)

    def _commit_record(self, identifier, value):
        commit = dict(format='skill-commit-v1', id=identifier,
                      record_sha256=hashlib.sha256(_encoded(value)).hexdigest())
        raw = _encoded(commit)
        key = self._key_for(identifier, 'commit.json')
        if self._head(key) is not None and self._read_record(key) != commit:
            raise RuntimeError('Skill COS 不可变保存记录冲突')
        self._immutable(key, io.BytesIO(raw), len(raw), hashlib.sha256(raw).hexdigest(), 'application/json')

    def store_repacked(self, identifier, path):
        record = self._archive(identifier, path)
        raw = _encoded(record)
        key = self._key_for(identifier, 'originals/' + record['sha256'] + '.json')
        self._immutable(key, io.BytesIO(raw), len(raw), hashlib.sha256(raw).hexdigest(), 'application/json')

    def confirm_local(self, record):
        save(self.data / 'skill-assets/confirmed' / (record['id'] + '.json'),
             dict(location=self.location, manifest_sha256=hashlib.sha256(_encoded(record)).hexdigest()))

    def _read_record(self, key):
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        raw = response['Body'].read(MAX_RECORD_BYTES + 1)
        if (len(raw) > MAX_RECORD_BYTES or response.get('ContentLength') != len(raw)
                or response.get('Metadata', {}).get('sha256') != hashlib.sha256(raw).hexdigest()):
            raise RuntimeError('Skill COS 记录摘要校验失败')
        return json.loads(raw)

    def _keys(self):
        paginator = self.client.get_paginator('list_objects_v2')
        return sorted(item['Key'] for page in paginator.paginate(Bucket=self.bucket, Prefix=self.skill_prefix)
                      for item in page.get('Contents', []))

    def restore(self, dashboard):
        from .skill_assets import IDENTIFIER
        try:
            keys = self._keys()
            records = [key for key in keys if key.endswith('/record.json')]
            if records:
                self._require_conditional_writes()
            available = {key[len(self.skill_prefix):].split('/')[0] for key in records}
            for key in keys:
                if key.endswith('/commit.json'):
                    identifier = key[len(self.skill_prefix):].split('/')[0]
                    if key != self._key_for(identifier, 'commit.json') or identifier not in available:
                        raise RuntimeError('Skill COS 已确认版本记录缺失，停止恢复')
            for receipt in (self.data / 'skill-assets/confirmed').glob('*.json'):
                if receipt.stem not in available:
                    raise RuntimeError('Skill COS 版本记录缺失，停止恢复')
            for key in records:
                identifier = key[len(self.skill_prefix):].split('/')[0]
                if not IDENTIFIER.fullmatch(identifier) or key != self._key_for(identifier, 'record.json'):
                    raise RuntimeError('Skill COS 版本路径无效')
                self._restore_version(dashboard, identifier, self._read_record(key), keys)
            self._verify_references(dashboard)
            return len(records)
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

    def _download_archive(self, identifier, record, target):
        from .skill_package import limits
        from .skill_assets import IDENTIFIER
        digest, size = record['sha256'], record['size']
        if (not isinstance(digest, str) or not IDENTIFIER.fullmatch(digest)
                or type(size) is not int or not 0 < size <= limits()['upload_max_bytes']):
            raise RuntimeError('Skill COS 原包记录无效')
        if target.exists() and (target.is_symlink() or not target.is_file()
                                or target.stat().st_size != size or _sha256(target) != digest):
            raise RuntimeError('Skill 本地不可变原包冲突，拒绝覆盖')
        self._download_object(self._key_for(identifier, 'archives/' + digest + '.zip'), target, size, digest)

    def _restore_version(self, dashboard, identifier, value, keys):
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
            self._download_archive(identifier, archive, stage / 'package.zip')
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
        self._restore_originals(identifier, record, keys)
        self._commit_record(identifier, value)
        self.confirm_local(record)

    def _restore_originals(self, identifier, record, keys):
        prefix = self._key_for(identifier, 'originals/')
        available = {record['archive_sha256']}
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
