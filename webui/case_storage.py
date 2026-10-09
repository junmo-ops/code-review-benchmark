"""Durable storage for uploaded case ZIP archives."""
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from runner.runtime import data_root

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'deployment' / 'case-storage.json'
ARCHIVE_ID = re.compile(r'^[a-f0-9]{64}$')


def _settings():
    try:
        value = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise RuntimeError('无法读取 deployment/case-storage.json') from None
    if not isinstance(value, dict) or value.get('mode') not in {'local', 'cos'}:
        raise RuntimeError('案例包存储配置无效')
    mode = os.environ.get('BENCH_CASE_STORAGE_MODE', value['mode']).strip().lower()
    if mode not in {'local', 'cos'}:
        raise RuntimeError('BENCH_CASE_STORAGE_MODE 须为 local 或 cos')
    s3 = value.get('s3')
    if not isinstance(s3, dict):
        raise RuntimeError('案例包 COS 配置无效')
    return mode, s3


def mode():
    return _settings()[0]


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _index_path(data):
    return Path(data) / 'case-archives' / 'index.json'


class CaseArchiveStorage:
    def __init__(self, data=None):
        _, options = _settings()
        self._configure_storage(options)
        self.data = Path(data) if data is not None else data_root(ROOT)
        self.object_prefix = f'{self.prefix}/v1/archives/'
        self.location = f'{self.endpoint}|{self.region}|{self.bucket}|{self.prefix}'
        self.cache = self.data / 'case-archives' / 'objects'
        self.index = _index_path(self.data)
        from webui.case_upload import limits
        self.max_bytes = limits()['upload_max_bytes']

    def _configure_storage(self, options):
        self.endpoint = str(options.get('endpoint', '')).strip()
        self.region = str(options.get('region', '')).strip()
        self.bucket = str(options.get('bucket', '')).strip()
        self.prefix = str(options.get('prefix', '')).strip().strip('/')
        self.timeout_ms = self._timeout(options)
        self._validate_storage_options()
        access_key, secret_key, session_token = self._credentials()
        self.client = self._create_client(access_key, secret_key, session_token)

    @staticmethod
    def _timeout(options):
        try:
            timeout_ms = int(options.get('timeout_ms', 600000))
        except (TypeError, ValueError):
            raise RuntimeError('案例包 COS 超时时间无效') from None

        if timeout_ms <= 0:
            raise RuntimeError('案例包 COS 超时时间无效')
        return timeout_ms

    def _validate_storage_options(self):
        parsed = urlsplit(self.endpoint)
        valid_endpoint = parsed.scheme in {'http', 'https'} and bool(parsed.netloc)
        safe_endpoint = not (parsed.username or parsed.password or parsed.query or parsed.fragment)
        safe_prefix = bool(self.prefix) and all(part not in {'', '.', '..'} for part in self.prefix.split('/'))
        if not valid_endpoint or not safe_endpoint or not self.region or not self.bucket or not safe_prefix:
            raise RuntimeError('案例包 COS 地址、区域、桶、前缀或超时时间无效')

    @staticmethod
    def _credentials():
        access_key = os.environ.get('BENCH_CASE_S3_ACCESS_KEY_ID', '').strip()
        secret_key = os.environ.get('BENCH_CASE_S3_SECRET_ACCESS_KEY', '').strip()
        session_token = os.environ.get('BENCH_CASE_S3_SESSION_TOKEN', '').strip() or None
        if not access_key or not secret_key:
            raise RuntimeError('缺少平台 Secret：BENCH_CASE_S3_ACCESS_KEY_ID 或 BENCH_CASE_S3_SECRET_ACCESS_KEY')
        return access_key, secret_key, session_token

    def _create_client(self, access_key, secret_key, session_token):
        try:
            import boto3
            from botocore.config import Config
            return boto3.client(
                's3', endpoint_url=self.endpoint, region_name=self.region,
                aws_access_key_id=access_key, aws_secret_access_key=secret_key,
                aws_session_token=session_token,
                config=Config(signature_version='s3v4', s3={'addressing_style': 'path'},
                              retries={'total_max_attempts': 1}, connect_timeout=15,
                              read_timeout=max(60, self.timeout_ms // 1000)))
        except Exception:
            raise RuntimeError('COS 客户端初始化失败，请检查服务依赖及平台 Secret') from None

    def _key(self, archive_id):
        if not ARCHIVE_ID.fullmatch(archive_id):
            raise ValueError('案例 ZIP 标识无效')
        return f'{self.object_prefix}{archive_id}.zip'

    def list_archives(self):
        try:
            paginator = self.client.get_paginator('list_objects_v2')
            result = []
            for page in paginator.paginate(Bucket=self.bucket, Prefix=self.object_prefix):
                for item in page.get('Contents', []):
                    key = item.get('Key', '')
                    name = key[len(self.object_prefix):]
                    if not name.endswith('.zip') or '/' in name or not ARCHIVE_ID.fullmatch(name[:-4]):
                        raise RuntimeError('COS 案例包专用前缀含有无法识别的对象')
                    size = item.get('Size')
                    if not isinstance(size, int) or not 0 < size <= self.max_bytes:
                        raise RuntimeError('COS 中的案例 ZIP 为空或超过当前上传上限')
                    result.append((name[:-4], size))
            return sorted(result)
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError('无法读取 COS 案例包清单，请检查网络和前缀权限') from None

    def store(self, archive):
        archive = Path(archive)
        size = archive.stat().st_size
        if not 0 < size <= self.max_bytes:
            raise RuntimeError('案例 ZIP 为空或超过当前上传上限')
        archive_id = _sha256(archive)
        key = self._key(archive_id)
        try:
            with archive.open('rb') as body:
                self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentLength=size,
                                       ContentType='application/zip', Metadata={'sha256': archive_id})
        except Exception:
            # COS may accept a PUT even if the client loses its response. Confirm
            # by deterministic object key, size and digest metadata before erroring.
            try:
                head = self.client.head_object(Bucket=self.bucket, Key=key)
                if (head.get('ContentLength') == size
                        and head.get('Metadata', {}).get('sha256') == archive_id):
                    return archive_id
            except Exception:
                pass
            raise RuntimeError('案例包已在当前工作台导入，但 COS 保存结果未确认；请检查连接后重新上传同一 ZIP') from None
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=key)
            if (head.get('ContentLength') != size
                    or head.get('Metadata', {}).get('sha256') != archive_id):
                raise RuntimeError('COS 回读校验不匹配')
        except Exception:
            raise RuntimeError('案例包已在当前工作台导入，但 COS 回读校验失败；请检查权限和连接后重新上传') from None
        return archive_id

    def _read_index(self):
        try:
            value = json.loads(self.index.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return set()
        except (OSError, ValueError):
            raise RuntimeError('案例包本地恢复记录损坏') from None
        if not isinstance(value, dict):
            raise RuntimeError('案例包本地恢复记录无效')
        old_location = value.get('location')
        if old_location and old_location != self.location:
            raise RuntimeError('案例包 COS 配置已变化；请使用新的 BENCH_DATA_ROOT 后重新启动')
        archives = value.get('archives')
        if not isinstance(archives, list) or any(not isinstance(item, str) or not ARCHIVE_ID.fullmatch(item) for item in archives):
            raise RuntimeError('案例包本地恢复记录无效')
        return set(archives)

    def _write_index(self, archives):
        self.index.parent.mkdir(parents=True, exist_ok=True)
        temp = self.index.with_suffix('.tmp')
        temp.write_text(json.dumps({'location': self.location, 'archives': sorted(archives)}, indent=2), encoding='utf-8')
        temp.replace(self.index)

    def _download(self, archive_id, expected_size):
        self.cache.mkdir(parents=True, exist_ok=True)
        target = self.cache / (archive_id + '.zip')
        if target.is_file() and target.stat().st_size == expected_size and _sha256(target) == archive_id:
            return target
        temp = target.with_suffix('.tmp')
        total = 0
        digest = hashlib.sha256()
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._key(archive_id))
            with temp.open('wb') as output:
                for chunk in response['Body'].iter_chunks(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > self.max_bytes or total > expected_size:
                        raise RuntimeError('COS 案例 ZIP 实际大小超过清单')
                    digest.update(chunk)
                    output.write(chunk)
            if total != expected_size or digest.hexdigest() != archive_id:
                raise RuntimeError('COS 案例 ZIP 校验失败')
            temp.replace(target)
            return target
        except Exception:
            temp.unlink(missing_ok=True)
            raise RuntimeError('无法下载或校验 COS 案例 ZIP，请检查对象完整性和读取权限') from None

    def restore(self, dashboard):
        from webui.case_assets import install
        archives = self.list_archives()
        installed = self._read_index()
        available = {archive_id for archive_id, _ in archives}
        if not installed.issubset(available):
            # Do not hide an object that was removed outside the workbench.
            raise RuntimeError('COS 案例包清单与本地恢复记录不一致，请检查专用前缀')
        for archive_id, size in archives:
            if archive_id in installed:
                continue
            archive = self._download(archive_id, size)
            install(dashboard, archive, persist=False)
            installed.add(archive_id)
            self._write_index(installed)
        return len(archives)

    def mark_installed(self, archive_id):
        installed = self._read_index()
        installed.add(archive_id)
        self._write_index(installed)


def persist_uploaded_archive(data, archive):
    if mode() != 'cos':
        return None
    storage = CaseArchiveStorage(data)
    archive_id = storage.store(archive)
    storage.mark_installed(archive_id)
    return archive_id


def restore_case_archives(root=ROOT):
    if mode() != 'cos':
        return 0
    from webui.service import Dashboard
    dashboard = Dashboard(root=root, load_jobs=False)
    storage = CaseArchiveStorage(dashboard.data)
    count = storage.restore(dashboard)
    print(f'COS 案例包恢复完成：{count} 份；未调用模型。', flush=True)
    return count
