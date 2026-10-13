"""Durable storage for uploaded case ZIP archives."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit
import zipfile

from runner.runtime import data_root

from runner.paths import PROJECT_ROOT as ROOT
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
        self.asset_cache = self.data / 'case-assets' / 'objects'
        self.index = _index_path(self.data)
        from webui.case_upload import limits
        self.max_bytes = limits()['upload_max_bytes']

    @property
    def assets_prefix(self):
        return f'{self.prefix}/v2/'

    def _configure_storage(self, options):
        self.endpoint = str(options.get('endpoint', '')).strip()
        self.region = str(options.get('region', '')).strip()
        self.bucket = str(options.get('bucket', '')).strip()
        self.prefix = str(options.get('prefix', '')).strip().strip('/')
        self.run_prefix = str(options.get('run_prefix', '')).strip().strip('/')
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
        safe_run_prefix = bool(self.run_prefix) and all(part not in {'', '.', '..'} for part in self.run_prefix.split('/'))
        if not valid_endpoint or not safe_endpoint or not self.region or not self.bucket or not safe_prefix or not safe_run_prefix:
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

    def _migration_key(self, archive_id):
        return f'{self.prefix}/v2/migrations/{archive_id}.json'

    def _asset_migration_done(self, archive_id):
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._migration_key(archive_id))
            return True
        except Exception:
            return False

    def _mark_asset_migrated(self, archive_id):
        body = json.dumps({'archive_sha256': archive_id, 'format': 'case-assets-v2'}).encode('utf-8')
        self.client.put_object(Bucket=self.bucket, Key=self._migration_key(archive_id), Body=body,
                               ContentType='application/json', Metadata={'sha256': hashlib.sha256(body).hexdigest()})

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
        restored = 0
        for archive_id, size in archives:
            if archive_id in installed:
                self._migrate_installed_archive(archive_id, size, dashboard.data)
                continue
            if self._asset_migration_done(archive_id):
                installed.add(archive_id)
                self._write_index(installed)
                continue
            archive = self._download(archive_id, size)
            install(dashboard, archive, persist=False)
            restored += 1
            if self._migrate_custom_archive(archive_id, archive, dashboard.data):
                self._mark_asset_migrated(archive_id)
            installed.add(archive_id)
            self._write_index(installed)
        return restored

    def _migrate_installed_archive(self, archive_id, size, data):
        if self._asset_migration_done(archive_id):
            return
        archive = self._download(archive_id, size)
        if self._migrate_custom_archive(archive_id, archive, data):
            self._mark_asset_migrated(archive_id)

    def _migrate_custom_archive(self, archive_id, archive, data):
        with zipfile.ZipFile(archive) as source:
            if 'manifest.json' not in source.namelist():
                return False
        CaseAssetStorage(data).store_archive_assets(archive, data)
        return True

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


def persist_case_upload(data, archive):
    storage_mode = mode()
    with zipfile.ZipFile(archive) as source:
        manifest = json.loads(source.read('manifest.json').decode('utf-8-sig')) if 'manifest.json' in source.namelist() else None
    if manifest and manifest.get('format') == 'code-review-benchmark-case-config-v1':
        if storage_mode == 'cos':
            return CaseAssetStorage(data).store_case_config_assets(archive, data)
        return None
    if storage_mode != 'cos':
        return None
    if manifest:
        return CaseAssetStorage(data).store_archive_assets(archive, data)
    return persist_uploaded_archive(data, archive)


def persist_repository_upload(data, versions):
    if mode() != 'cos':
        return None
    return CaseAssetStorage(data).store_repository_versions(data, versions)


class CaseAssetStorage(CaseArchiveStorage):
    """Store immutable dataset manifests and Git bundles as separate COS objects."""

    def _dataset_prefix(self, suite, digest):
        return f'{self.assets_prefix}datasets/{suite}/{digest}/'

    def _repository_key(self, slug, digest):
        return f'{self.assets_prefix}repositories/{slug}/{digest}.bundle'

    def _repository_pool_key(self, slug, digest):
        return f'{self.prefix}/v3/repositories/{slug}/{digest}.bundle'

    def restore_repository_bundle(self, slug, record):
        target = self.asset_cache / (record['sha256'] + '.bundle')
        self._download_object(self._repository_pool_key(slug, record['sha256']), target,
                              record['size'], record['sha256'])
        return target

    def rename_dataset(self, suite, digest, name):
        self._update_dataset_record(suite, digest, {'name': name})

    def archive_dataset(self, suite, digest):
        self._update_dataset_record(suite, digest, {'archived': True})

    def _update_dataset_record(self, suite, digest, changes):
        path = self._local_record_path(self.data, suite, digest)
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise RuntimeError('找不到案例集的 COS 记录，无法更新资源') from None
        self._validate_record(record, self._dataset_prefix(suite, digest) + 'record.json')
        record.update(changes)
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True).encode('utf-8')
        self._put_stream(self._dataset_prefix(suite, digest) + 'record.json', io.BytesIO(encoded), len(encoded),
                         hashlib.sha256(encoded).hexdigest(), 'application/json')
        self._save_local_record(self.data, record)

    def delete_dataset(self, suite, digest):
        for suffix in ('record.json', 'dataset.jsonl'):
            self.client.delete_object(Bucket=self.bucket, Key=self._dataset_prefix(suite, digest) + suffix)
        self._local_record_path(self.data, suite, digest).unlink(missing_ok=True)

    def delete_repository(self, slug, versions, registry):
        self._upload_repository_registry(registry)
        for version in versions:
            digest = version.get('sha256') if isinstance(version, dict) else None
            if isinstance(digest, str) and ARCHIVE_ID.fullmatch(digest):
                self.client.delete_object(Bucket=self.bucket, Key=self._repository_pool_key(slug, digest))
        self._save_repository_registry(self.data, registry)

    def store_repository_archive(self, archive, data):
        from .case_assets import repository_registry
        registry = repository_registry(data)
        with zipfile.ZipFile(archive) as source:
            manifest = json.loads(source.read('manifest.json').decode('utf-8-sig'))
            for slug, repo in manifest['repositories'].items():
                record = manifest['files'][f'bundles/{slug}.bundle']
                with source.open(f'bundles/{slug}.bundle') as bundle:
                    self._put_stream(self._repository_pool_key(slug, record['sha256']), bundle,
                                     record['size'], record['sha256'], 'application/octet-stream')
                entry = registry['repositories'].get(slug, {'repo': repo['name'], 'versions': [], 'current': ''})
                if entry.get('repo') != repo['name'] or not isinstance(entry.get('versions'), list):
                    raise RuntimeError('仓库名称与 COS 中已有资源不一致：' + slug)
                version = {'sha256': record['sha256'], 'size': record['size'],
                           'file': f'case-repositories/objects/{record["sha256"]}.bundle'}
                if not any(item['sha256'] == version['sha256'] for item in entry['versions']):
                    entry['versions'].append(version)
                entry['current'] = version['sha256']
                registry['repositories'][slug] = entry
            self._upload_repository_registry(registry)
        self._save_repository_registry(data, registry)

    def store_repository_versions(self, data, versions):
        from .case_assets import repository_registry
        registry = repository_registry(data)
        for slug, record in versions.items():
            path = Path(data) / 'case-repositories' / 'objects' / (record['sha256'] + '.bundle')
            if path.stat().st_size != record['size'] or _sha256(path) != record['sha256']:
                raise RuntimeError('本地仓库 bundle 校验失败：' + slug)
            with path.open('rb') as bundle:
                self._put_stream(self._repository_pool_key(slug, record['sha256']), bundle,
                                 record['size'], record['sha256'], 'application/octet-stream')
        self._upload_repository_registry(registry)
        self._save_repository_registry(data, registry)

    def _upload_repository_registry(self, registry):
        encoded = json.dumps(registry, ensure_ascii=False, sort_keys=True).encode('utf-8')
        key = f'{self.prefix}/v3/repositories/registry.json'
        self._put_stream(key, io.BytesIO(encoded), len(encoded), hashlib.sha256(encoded).hexdigest(),
                         'application/json')

    def _save_repository_registry(self, data, registry):
        from .case_assets import repository_registry_path
        target = repository_registry_path(data)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(registry, ensure_ascii=False, sort_keys=True), encoding='utf-8')
        temporary.replace(target)

    def _restore_repository_registry(self, data):
        key = f'{self.prefix}/v3/repositories/registry.json'
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            raw = response['Body'].read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError
            value = json.loads(raw.decode('utf-8'))
            if not isinstance(value, dict) or not isinstance(value.get('repositories'), dict):
                raise ValueError
        except Exception as error:
            if getattr(error, 'response', {}).get('ResponseMetadata', {}).get('HTTPStatusCode') == 404:
                return
            raise RuntimeError('无法恢复 COS 仓库资源清单') from None
        self._save_repository_registry(data, value)

    def store_case_config_assets(self, archive, data):
        from .case_assets import _registry
        from runner.cloud import checked_instances
        with zipfile.ZipFile(archive) as source:
            manifest = json.loads(source.read('manifest.json').decode('utf-8-sig'))
            suite = manifest['suite']
            digest = manifest['files']['dataset.jsonl']['sha256']
            dataset_path = Path(data) / 'case-datasets' / suite / digest / 'dataset.jsonl'
            cases = checked_instances(dataset_path, suite)
            row = next((item for item in _registry(data)['datasets']
                        if item.get('suite') == suite and item.get('dataset_sha256') == digest), None)
            refs = row.get('repository_versions') if row else None
            if not isinstance(refs, dict) or not refs:
                raise RuntimeError('案例配置未固定仓库版本')
            repositories = {slug: dict(item) for slug, item in refs.items()}
            self._store_dataset_record(data, row['name'], suite, digest, dataset_path, cases, repositories)

    def _store_dataset_record(self, data, name, suite, digest, dataset_path, cases, repositories):
        dataset_key = self._dataset_prefix(suite, digest) + 'dataset.jsonl'
        with dataset_path.open('rb') as stream:
            self._put_stream(dataset_key, stream, dataset_path.stat().st_size, digest, 'application/x-ndjson')
        entry = {'format': 'code-review-benchmark-case-assets-v2', 'name': name.strip(),
                 'suite': suite, 'count': len(cases), 'dataset_sha256': digest,
                 'dataset_size': dataset_path.stat().st_size, 'repositories': repositories}
        encoded = json.dumps(entry, ensure_ascii=False, sort_keys=True).encode('utf-8')
        self._put_stream(self._dataset_prefix(suite, digest) + 'record.json', io.BytesIO(encoded), len(encoded),
                         hashlib.sha256(encoded).hexdigest(), 'application/json')
        self._save_local_record(data, entry)

    def _local_record_path(self, data, suite, digest):
        return Path(data) / 'case-assets' / 'records' / suite / (digest + '.json')

    def _save_local_record(self, data, record):
        target = self._local_record_path(data, record['suite'], record['dataset_sha256'])
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.tmp')
        temporary.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True), encoding='utf-8')
        temporary.replace(target)

    def _put_stream(self, key, stream, size, digest, content_type):
        metadata = {'sha256': digest}
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=key)
            if head.get('ContentLength') == size and head.get('Metadata', {}).get('sha256') == digest:
                return
        except Exception:
            pass
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=stream, ContentLength=size,
                                   ContentType=content_type, Metadata=metadata)
            head = self.client.head_object(Bucket=self.bucket, Key=key)
            if head.get('ContentLength') != size or head.get('Metadata', {}).get('sha256') != digest:
                raise RuntimeError('COS 对象回读校验不匹配')
        except Exception:
            raise RuntimeError('案例资源未能可靠保存到 COS；请检查专用前缀权限和连接') from None

    def store_archive_assets(self, archive, data):
        from runner.cloud import checked_instances
        with zipfile.ZipFile(archive) as source:
            manifest = json.loads(source.read('manifest.json').decode('utf-8-sig'))
            suite = manifest['suite']
            dataset_record = manifest['files']['dataset.jsonl']
            digest = dataset_record['sha256']
            with tempfile.TemporaryDirectory(prefix='case-upload-check-', dir=data) as name:
                dataset_path = Path(name) / 'dataset.jsonl'
                with source.open('dataset.jsonl') as member, dataset_path.open('wb') as output:
                    shutil.copyfileobj(member, output, length=1024 * 1024)
                if _sha256(dataset_path) != digest:
                    raise RuntimeError('案例清单 SHA256 校验失败')
                cases = checked_instances(dataset_path, suite)
            from .case_assets import _registry
            row = next((item for item in _registry(data)['datasets']
                        if item.get('suite') == suite and item.get('dataset_sha256') == digest), None)
            if row is None:
                raise RuntimeError('本地案例集注册记录缺失')
            local_record = self._local_record_path(data, suite, digest)
            dataset_name = row['name']
            if local_record.is_file():
                dataset_name = self._validate_existing_local_record(local_record, suite, digest)['name']
            registered_dataset = Path(data) / row['file']
            repositories = {}
            for slug in sorted({case.repo.replace('/', '__') for case in cases}):
                name = f'bundles/{slug}.bundle'
                record = self._pinned_repository_record(row, slug, manifest['files'][name])
                pool_key = self._repository_pool_key(slug, record['sha256'])
                bundle_path = Path(data) / 'case-repositories' / 'objects' / (record['sha256'] + '.bundle')
                if (bundle_path.is_file() and bundle_path.stat().st_size == record['size']
                        and _sha256(bundle_path) == record['sha256']):
                    with bundle_path.open('rb') as bundle:
                        self._put_stream(pool_key, bundle, record['size'], record['sha256'],
                                         'application/octet-stream')
                else:
                    if record['sha256'] != manifest['files'][name]['sha256']:
                        raise RuntimeError('本地固定仓库版本缺失，拒绝改写案例集版本绑定：' + slug)
                    with source.open(name) as bundle:
                        self._put_stream(pool_key, bundle, record['size'], record['sha256'],
                                         'application/octet-stream')
                repositories[slug] = {'sha256': record['sha256'], 'size': record['size'],
                                      'source': 'repository-pool'}
            from .case_assets import repository_registry
            self._upload_repository_registry(repository_registry(data))
            self._store_dataset_record(data, dataset_name, suite, digest, registered_dataset, cases, repositories)

    def _validate_existing_local_record(self, path, suite, digest):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            self._validate_record(record, self._dataset_prefix(suite, digest) + 'record.json')
        except (OSError, ValueError, KeyError, TypeError):
            raise RuntimeError('已有案例集 COS 记录无效，拒绝覆盖其仓库版本绑定') from None
        return record

    @staticmethod
    def _pinned_repository_record(row, slug, archive_record):
        references = row.get('repository_versions')
        pinned = references.get(slug, {}) if isinstance(references, dict) else {}
        digest, size = pinned.get('sha256'), pinned.get('size')
        if not isinstance(digest, str) or not ARCHIVE_ID.fullmatch(digest) or type(size) is not int or size <= 0:
            digest, size = archive_record['sha256'], archive_record['size']
        return {'sha256': digest, 'size': size}

    def _download_object(self, key, target, size, digest):
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.stat().st_size == size and _sha256(target) == digest:
            return
        temporary = target.with_name(target.name + '.part')
        total, checksum = 0, hashlib.sha256()
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            with temporary.open('wb') as output:
                for chunk in response['Body'].iter_chunks(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > size:
                        raise RuntimeError('COS 案例资源超过清单大小')
                    checksum.update(chunk)
                    output.write(chunk)
            if total != size or checksum.hexdigest() != digest:
                raise RuntimeError('COS 案例资源 SHA256 校验失败')
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise RuntimeError('无法下载或校验 COS 案例资源，请检查对象和读取权限') from None

    def _records(self):
        prefix = self.assets_prefix + 'datasets/'
        try:
            paginator = self.client.get_paginator('list_objects_v2')
            return sorted(item['Key'] for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix)
                          for item in page.get('Contents', []) if item['Key'].endswith('/record.json'))
        except Exception:
            raise RuntimeError('无法读取 COS 案例资源清单，请检查网络和前缀权限') from None

    def _restore_record(self, dashboard, key):
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            raw = response['Body'].read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError
            record = json.loads(raw.decode('utf-8'))
            self._validate_record(record, key)
        except Exception:
            raise RuntimeError('COS 案例资源清单格式无效') from None
        record = self._migrate_legacy_repository_refs(record, dashboard.data)
        self._restore_record_files(dashboard, record)

    def _migrate_legacy_repository_refs(self, record, data):
        legacy = [(slug, item) for slug, item in record['repositories'].items()
                  if item.get('source', 'case-archive') == 'case-archive']
        if not legacy:
            return record
        from .case_assets import _register_repository_version, repository_registry
        from .service import save
        registry = repository_registry(data)
        for slug, item in legacy:
            existing = registry['repositories'].get(slug, {})
            current = existing.get('current') if isinstance(existing, dict) else None
            target = self.asset_cache / (item['sha256'] + '.bundle')
            self._download_object(self._repository_key(slug, item['sha256']), target,
                                  item['size'], item['sha256'])
            key = self._repository_pool_key(slug, item['sha256'])
            with target.open('rb') as bundle:
                self._put_stream(key, bundle, item['size'], item['sha256'], 'application/octet-stream')
            pool_record = {'sha256': item['sha256'], 'size': item['size'],
                           'file': f'case-repositories/objects/{item["sha256"]}.bundle'}
            _register_repository_version(registry, slug, slug.replace('__', '/'), pool_record)
            if current and current != item['sha256']:
                registry['repositories'][slug]['current'] = current
            item['source'] = 'repository-pool'
        self._upload_repository_registry(registry)
        from .case_assets import repository_registry_path
        save(repository_registry_path(data), registry)
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True).encode('utf-8')
        self._put_stream(self._dataset_prefix(record['suite'], record['dataset_sha256']) + 'record.json',
                         io.BytesIO(encoded), len(encoded), hashlib.sha256(encoded).hexdigest(),
                         'application/json')
        return record

    def _validate_record(self, record, key):
        if not isinstance(record, dict):
            raise ValueError
        suite, digest = record.get('suite'), record.get('dataset_sha256')
        if 'archived' in record and type(record['archived']) is not bool:
            raise ValueError('案例集移除标记必须为布尔值')
        if not self._valid_record_identity(record, suite, digest, key):
            raise ValueError
        from .case_upload import limits
        maximum = limits()['unpacked_max_bytes']
        repos = record.get('repositories')
        dataset_size = record.get('dataset_size')
        if (not self._valid_repository_records(repos, maximum)
                or type(record.get('count')) is not int or record['count'] <= 0
                or not isinstance(record.get('name'), str) or not record['name'].strip()
                or type(dataset_size) is not int or not 0 < dataset_size <= maximum):
            raise ValueError

    def _valid_record_identity(self, record, suite, digest, key):
        return (record.get('format') == 'code-review-benchmark-case-assets-v2'
                and suite in {'aacr', 'swe_prbench_llm', 'codefuse_cr'}
                and isinstance(digest, str) and ARCHIVE_ID.fullmatch(digest)
                and key == self._dataset_prefix(suite, digest) + 'record.json')

    @staticmethod
    def _valid_repository_records(repositories, maximum):
        if not isinstance(repositories, dict) or not repositories:
            return False
        for slug, item in repositories.items():
            if (not isinstance(slug, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', slug)
                    or not isinstance(item, dict) or not isinstance(item.get('sha256'), str)
                    or not ARCHIVE_ID.fullmatch(item['sha256']) or type(item.get('size')) is not int
                    or not 0 < item['size'] <= maximum
                    or item.get('source', 'case-archive') not in {'case-archive', 'repository-pool'}):
                return False
        return True

    def _restore_record_files(self, dashboard, record):
        from runner.cloud import checked_instances
        from .case_assets import _registry
        from .service import save
        suite, digest = record['suite'], record['dataset_sha256']
        folder = dashboard.data / 'case-datasets' / suite / digest
        dataset = folder / 'dataset.jsonl'
        self._download_object(self._dataset_prefix(suite, digest) + 'dataset.jsonl', dataset,
                              record['dataset_size'], digest)
        cases = checked_instances(dataset, suite)
        expected = {case.repo.replace('/', '__') for case in cases}
        if len(cases) != record.get('count') or expected != set(record['repositories']):
            raise RuntimeError('COS 案例清单中的案例与仓库资源不匹配')
        self._save_local_record(dashboard.data, record)
        registry = _registry(dashboard.data)
        versions = record['repositories']
        entry = dict(id=f'uploads/{suite}/{digest}.jsonl', suite=suite, name=record['name'],
                     count=len(cases), dataset_sha256=digest,
                     file=dataset.relative_to(dashboard.data).as_posix(), repositories=sorted(expected),
                     repository_versions=versions, archived=bool(record.get('archived', False)))
        existing = next((row for row in registry['datasets']
                         if row.get('suite') == suite and row.get('dataset_sha256') == digest), None)
        if existing:
            # Keep dataset IDs used by saved forms and comparison manifests.
            entry['id'] = existing['id']
            existing.update(entry)
        else:
            registry['datasets'].append(entry)
        save(dashboard.data / 'case-datasets' / 'registry.json', registry)

    def prepare_dataset_repositories(self, data, suite, digest):
        from runner.cloud import checked_instances
        from .case_assets import _prepare_repositories
        path = self._local_record_path(data, suite, digest)
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            self._validate_record(record, self._dataset_prefix(suite, digest) + 'record.json')
        except (OSError, ValueError, KeyError, TypeError):
            raise RuntimeError('找不到该案例集在 COS 中的仓库资源清单；请确认案例集已成功导入') from None
        dataset = self._registered_dataset_path(data, suite, digest)
        cases = checked_instances(dataset, suite)
        with tempfile.TemporaryDirectory(prefix='case-assets-', dir=data) as name:
            staging = Path(name)
            for slug, item in record['repositories'].items():
                cached_bundle = self.asset_cache / (item['sha256'] + '.bundle')
                object_key = (self._repository_pool_key(slug, item['sha256'])
                              if item.get('source') == 'repository-pool'
                              else self._repository_key(slug, item['sha256']))
                self._download_object(object_key, cached_bundle,
                                      item['size'], item['sha256'])
                repo_bundle = staging / 'bundles' / (slug + '.bundle')
                repo_bundle.parent.mkdir(parents=True, exist_ok=True)
                repo_bundle.symlink_to(cached_bundle)
            _prepare_repositories(cases, staging / 'bundles', Path(data) / 'repos')

    @staticmethod
    def _registered_dataset_path(data, suite, digest):
        from .case_assets import _registry
        row = next((item for item in _registry(data)['datasets']
                    if item.get('suite') == suite and item.get('dataset_sha256') == digest), None)
        if row is None:
            raise RuntimeError('案例集未注册，请重新导入案例包')
        base = (Path(data) / 'case-datasets').resolve()
        original = Path(data) / row['file']
        path = original.resolve()
        if original.is_symlink() or not path.is_relative_to(base) or not path.is_file():
            raise RuntimeError('已注册案例集文件不可用，请重新导入案例包')
        if _sha256(path) != digest:
            raise RuntimeError('已注册案例集 SHA256 校验失败')
        return path

    def restore(self, dashboard):
        self._restore_repository_registry(dashboard.data)
        records = self._records()
        for key in records:
            self._restore_record(dashboard, key)
        return len(records)


def restore_case_archives(root=ROOT):
    if mode() != 'cos':
        return 0
    from webui.service import Dashboard
    dashboard = Dashboard(root=root, load_jobs=False)
    asset_count = CaseAssetStorage(dashboard.data).restore(dashboard)
    archive_count = CaseArchiveStorage(dashboard.data).restore(dashboard)
    count = asset_count + archive_count
    print(f'COS 案例资源恢复完成：{asset_count} 份拆分案例集和 {archive_count} 份旧 ZIP；未调用模型。', flush=True)
    return count
