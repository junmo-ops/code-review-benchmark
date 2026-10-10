"""Persist model profiles in a private COS prefix for container recovery."""
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from runner.runtime import config_root, data_root

ROOT = Path(__file__).resolve().parents[1]
PROFILE_NAME = re.compile(r'^model(?:[A-Za-z0-9._-]{0,180})\.local\.json$')
MAX_PROFILE_BYTES = 128 * 1024


class ModelProfileStorage:
    def __init__(self, root=ROOT):
        from webui.case_storage import CaseArchiveStorage
        case_storage = CaseArchiveStorage(data_root(root))
        self.client = case_storage.client
        self.bucket = case_storage.bucket
        parent = case_storage.prefix.rsplit('/', 1)[0]
        self.prefix = f'{parent}/model-configs/v1/profiles/'
        self.configs = config_root(root)

    @staticmethod
    def _valid_name(filename):
        return (isinstance(filename, str) and PROFILE_NAME.fullmatch(filename)
                and '..' not in filename and '/' not in filename and '\\' not in filename)

    def _key(self, filename):
        if not self._valid_name(filename):
            raise RuntimeError('模型配置文件名无效')
        digest = hashlib.sha256(filename.encode('utf-8')).hexdigest()
        return self.prefix + digest + '.json'

    def _payload(self, filename):
        from local import ALLOWED
        path = self.configs / filename
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('模型配置文件不存在或不是普通文件')
        if path.stat().st_size > MAX_PROFILE_BYTES:
            raise RuntimeError('模型配置文件超过存储上限')
        raw = path.read_bytes()
        if len(raw) > MAX_PROFILE_BYTES:
            raise RuntimeError('模型配置文件超过存储上限')
        try:
            config = json.loads(raw.decode('utf-8'))
        except (UnicodeError, ValueError):
            raise RuntimeError('模型配置文件格式无效') from None
        if (not isinstance(config, dict) or set(config) - ALLOWED
                or any(not isinstance(k, str) or not isinstance(v, str) for k, v in config.items())):
            raise RuntimeError('模型配置字段无效')
        label_path = path.with_suffix('.label')
        label = ''
        if label_path.exists():
            if label_path.is_symlink() or not label_path.is_file():
                raise RuntimeError('模型显示名称文件无效')
            label = label_path.read_text(encoding='utf-8')
            if len(label) > 256:
                raise RuntimeError('模型显示名称过长')
        return {'schema': 1, 'filename': filename, 'config': config, 'label': label}

    def store(self, filename):
        payload = json.dumps(self._payload(filename), ensure_ascii=False,
                             separators=(',', ':')).encode('utf-8')
        if len(payload) > MAX_PROFILE_BYTES:
            raise RuntimeError('模型配置文件超过存储上限')
        digest = hashlib.sha256(payload).hexdigest()
        key = self._key(filename)
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=payload,
                                   ContentLength=len(payload), ContentType='application/json',
                                   Metadata={'sha256': digest})
            head = self.client.head_object(Bucket=self.bucket, Key=key)
            if (head.get('ContentLength') != len(payload)
                    or head.get('Metadata', {}).get('sha256') != digest):
                raise RuntimeError('COS 回读校验不匹配')
        except Exception:
            try:
                head = self.client.head_object(Bucket=self.bucket, Key=key)
                if (head.get('ContentLength') == len(payload)
                        and head.get('Metadata', {}).get('sha256') == digest):
                    return
            except Exception:
                pass
            raise RuntimeError('模型配置未能可靠保存到 COS；请检查私有前缀的读写权限后重试') from None

    def list_profiles(self):
        try:
            paginator = self.client.get_paginator('list_objects_v2')
            return [item['Key'] for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix)
                    for item in page.get('Contents', [])]
        except Exception:
            raise RuntimeError('无法读取 COS 模型配置，请检查私有前缀权限和网络') from None

    def restore(self, key):
        payload = self._read_payload(key)
        filename, config, label = self._validated_payload(payload, key)
        self._write_local(filename, config, label)
        return filename

    def _read_payload(self, key):
        if not isinstance(key, str) or not key.startswith(self.prefix) or not key.endswith('.json'):
            raise RuntimeError('COS 模型配置对象路径无效')
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            body = response['Body'].read(MAX_PROFILE_BYTES + 1)
            if len(body) > MAX_PROFILE_BYTES:
                raise RuntimeError('COS 模型配置文件超过存储上限')
            metadata = response.get('Metadata', {}).get('sha256')
            if not metadata or hashlib.sha256(body).hexdigest() != metadata:
                raise RuntimeError('COS 模型配置校验失败')
            payload = json.loads(body.decode('utf-8'))
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError('无法读取或校验 COS 模型配置') from None
        return payload

    def _validated_payload(self, payload, key):
        filename = payload.get('filename') if isinstance(payload, dict) else None
        config = payload.get('config') if isinstance(payload, dict) else None
        label = payload.get('label', '') if isinstance(payload, dict) else ''
        from local import ALLOWED
        if not isinstance(payload, dict) or payload.get('schema') != 1:
            raise RuntimeError('COS 模型配置内容无效')
        if not self._valid_name(filename) or self._key(filename) != key:
            raise RuntimeError('COS 模型配置文件名无效')
        if not isinstance(config, dict) or set(config) - ALLOWED:
            raise RuntimeError('COS 模型配置字段无效')
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in config.items()):
            raise RuntimeError('COS 模型配置字段类型无效')
        if not isinstance(label, str) or len(label) > 256:
            raise RuntimeError('COS 模型配置内容无效')
        return filename, config, label

    def _write_local(self, filename, config, label):
        self.configs.mkdir(parents=True, exist_ok=True)
        path = self.configs / filename
        label_path = path.with_suffix('.label')
        serialized = (json.dumps(config, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file():
                raise RuntimeError('本地模型配置路径不是普通文件，已停止恢复')
            try:
                matches = json.loads(path.read_text(encoding='utf-8')) == config
            except (OSError, ValueError):
                matches = False
            if not matches:
                raise RuntimeError('本地模型配置与 COS 版本冲突；为避免覆盖配置，已停止启动')
        else:
            self._atomic_private_write(path, serialized)
        if label:
            label_data = label.encode('utf-8')
            if label_path.exists() or label_path.is_symlink():
                if (label_path.is_symlink() or not label_path.is_file()
                        or label_path.read_bytes() != label_data):
                    raise RuntimeError('本地模型名称与 COS 版本冲突；为避免覆盖配置，已停止启动')
            else:
                self._atomic_private_write(label_path, label_data)

    @staticmethod
    def _atomic_private_write(path, data):
        temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as output:
                output.write(data)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def persist_profile(root, filename):
    from webui.case_storage import mode
    if mode() != 'cos':
        return False
    ModelProfileStorage(root).store(filename)
    return True


def restore_profiles(root=ROOT):
    from webui.case_storage import mode
    if mode() != 'cos':
        return 0
    storage = ModelProfileStorage(root)
    keys = storage.list_profiles()
    local_files = sorted(path for path in storage.configs.glob('model*.local.json')
                         if not path.is_symlink() and storage._valid_name(path.name))
    remote_names = set()
    for key in keys:
        remote_names.add(storage.restore(key))
    migrated = 0
    for path in local_files:
        if path.name not in remote_names:
            storage.store(path.name)
            migrated += 1
    restored = len(remote_names)
    if migrated:
        print(f'已将 {migrated} 份现有模型配置迁移到 COS。', flush=True)
    if restored:
        print(f'已从 COS 恢复 {restored} 份模型配置。', flush=True)
    return restored + migrated
