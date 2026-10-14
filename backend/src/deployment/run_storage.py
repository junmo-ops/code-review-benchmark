"""Content-addressed COS persistence for benchmark run evidence and job logs."""
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone

from runner.runtime import data_root

from runner.paths import PROJECT_ROOT as ROOT
RUN_ID = re.compile(r'^[A-Za-z0-9_-]{1,100}$')
JOB_ID = re.compile(r'^[a-f0-9]{32}$')
SHA256 = re.compile(r'^[a-f0-9]{64}$')
SKIP_NAMES = {'.cloud.lock', 'stop-request.json'}


def _now():
    return datetime.now(timezone.utc).isoformat()


def request_run_sync(data):
    folder = Path(data) / 'ui-state' / 'run-sync-requests'
    folder.mkdir(parents=True, exist_ok=True)
    request = folder / (uuid.uuid4().hex + '.json')
    request.write_text('{}', encoding='utf-8')


@contextmanager
def _storage_lock(data):
    import fcntl
    folder = Path(data) / 'ui-state'
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'run-storage.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def _trim_jsonl_snapshot(path):
    with path.open('r+b') as stream:
        stream.seek(0, 2)
        end = stream.tell()
        while end > 0:
            start = max(0, end - 8192)
            stream.seek(start)
            chunk = stream.read(end - start)
            newline = chunk.rfind(b'\n')
            if newline >= 0:
                stream.truncate(start + newline + 1)
                return
            end = start
        stream.truncate(0)


class RunDataStorage:
    def __init__(self, root=ROOT):
        from webui.case_storage import CaseArchiveStorage
        case_storage = CaseArchiveStorage(data_root(root))
        self.client = case_storage.client
        self.bucket = case_storage.bucket
        self.prefix = case_storage.run_prefix.rstrip('/')
        self.data = data_root(root)
        self.manifest_prefix = f'{self.prefix}/manifests/'
        self.object_prefix = f'{self.prefix}/objects/'
        self.manifests = {}
        self.uploaded = set()
        self.observed = {}
        self.preserved_files = 0
        self.stable_seconds = max(5, int(os.environ.get('BENCH_RUN_STORAGE_STABLE_SECONDS', '15')))

    def _manifest_key(self, group):
        return f'{self.manifest_prefix}{group}.json'

    def _object_key(self, digest):
        return f'{self.object_prefix}{digest[:2]}/{digest}'

    @staticmethod
    def _safe_group(group):
        parts = group.split('/')
        if len(parts) == 2 and parts[0] == 'runs' and RUN_ID.fullmatch(parts[1]):
            return True
        if len(parts) == 3 and parts[0] == 'runs' and parts[1] in {'mcr', 'ccrab'} and RUN_ID.fullmatch(parts[2]):
            return True
        if group in {'state/queue', 'state/lcr-sessions'}:
            return True
        return len(parts) == 2 and parts[0] == 'jobs' and JOB_ID.fullmatch(parts[1]) is not None

    def _groups(self):
        sessions = self.data / 'home/.leancodereview/sessions'
        if sessions.is_dir():
            yield 'state/lcr-sessions', sessions
        runs = self.data / 'runs'
        yield from self._run_groups(runs)
        jobs = self.data / 'ui-state' / 'jobs'
        yield from self._job_groups(jobs)

    def _run_groups(self, runs):
        if not runs.is_dir():
            return
        for folder in sorted(runs.iterdir()):
            if not folder.is_dir() or folder.is_symlink():
                continue
            group = 'runs/' + folder.name
            if folder.name in {'mcr', 'ccrab'}:
                yield from self._nested_run_groups(group, folder)
            elif self._safe_group(group):
                yield group, folder

    def _nested_run_groups(self, group, folder):
        for nested in sorted(folder.iterdir()):
            key = group + '/' + nested.name
            if nested.is_dir() and not nested.is_symlink() and self._safe_group(key):
                yield key, nested

    def _job_groups(self, jobs):
        if not jobs.is_dir():
            return
        for path in sorted(jobs.iterdir()):
            if path.is_file() and not path.is_symlink() and path.suffix in {'.json', '.log'}:
                group = 'jobs/' + path.stem
                if self._safe_group(group):
                    yield group, path

    def _walk(self, group, root):
        if root.is_file():
            yield group, root, root.name
            return
        from runner.evidence import evidence_files
        for path in evidence_files(root):
            if path.is_symlink() or not path.is_file() or path.name in SKIP_NAMES or path.name.endswith('.tmp'):
                continue
            relative = path.relative_to(root)
            if 'workspaces' in relative.parts:
                continue
            yield group, path, relative.as_posix()

    def _stable(self, path, force):
        stat = path.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        seen = self.observed.get(path)
        if seen is None or seen[0] != signature:
            self.observed[path] = (signature, time.monotonic())
            return force
        return force or time.monotonic() - seen[1] >= self.stable_seconds

    def _put_object(self, path, digest):
        key = self._object_key(digest)
        if digest in self.uploaded:
            return
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=key)
            if head.get('Metadata', {}).get('sha256') == digest:
                self.uploaded.add(digest)
                return
        except Exception:
            pass
        size = path.stat().st_size
        with path.open('rb') as body:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentLength=size,
                                   ContentType='application/octet-stream', Metadata={'sha256': digest})
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        if head.get('ContentLength') != size or head.get('Metadata', {}).get('sha256') != digest:
            raise RuntimeError('运行数据对象回读校验失败')
        self.uploaded.add(digest)

    @staticmethod
    def _copy_prefix(source, target, size):
        remaining = size
        while remaining:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                return False
            target.write(chunk)
            remaining -= len(chunk)
        return True

    @contextmanager
    def _file_snapshot(self, path, appendable):
        before = path.stat()
        folder = self.data / 'ui-state'
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix='.run-file-', dir=folder, delete=False) as output:
            snapshot = Path(output.name)
            try:
                with path.open('rb') as source:
                    copied = self._copy_prefix(source, output, before.st_size)
            except BaseException:
                snapshot.unlink(missing_ok=True)
                raise
        try:
            after = path.stat()
            same_file = (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
            valid = after.st_size >= before.st_size if appendable else (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
            yield snapshot if copied and same_file and valid else None
        finally:
            snapshot.unlink(missing_ok=True)

    def _sync_file(self, path, previous, force):
        appendable = path.name.endswith(('.log', '.stdout.json', '.stdout.txt', '.stderr.txt', '.jsonl'))
        if not appendable and not self._stable(path, force):
            return None
        with self._file_snapshot(path, appendable) as snapshot:
            if snapshot is None:
                return None
            if path.suffix == '.jsonl':
                _trim_jsonl_snapshot(snapshot)
            if '.leancodereview' in path.parts:
                from .session_checkpoint import sanitize_checkpoint
                try:
                    from runner.diagnostics import phase
                    with phase('backup.sanitize_checkpoint', bytes=snapshot.stat().st_size):
                        sanitize_checkpoint(snapshot, getattr(self, 'checkpoint_secrets', ()))
                except (ValueError, UnicodeError):
                    self.checkpoint_errors = getattr(self, 'checkpoint_errors', 0) + 1
                    print('LCR 会话检查点损坏，保留旧备份；答卷与任务日志继续同步。', flush=True)
                    return None
            digest = _digest(snapshot)
            record = {'sha256': digest, 'size': snapshot.stat().st_size}
            if previous != record:
                self._put_object(snapshot, digest)
            return record

    def _previous_manifest(self, group):
        key = self._manifest_key(group)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as error:
            code = getattr(error, 'response', {}).get('Error', {}).get('Code')
            if code in {'NoSuchKey', '404', 'NotFound'}:
                return {'version': 1, 'files': {}}
            raise
        manifest = json.loads(response['Body'].read().decode('utf-8'))
        if manifest.get('version') != 1 or manifest.get('group') != group or not isinstance(manifest.get('files'), dict):
            raise RuntimeError('COS 运行数据清单格式无效')
        return manifest

    def _commit_group(self, group, root, force):
        previous = self.manifests.get(group)
        if previous is None:
            previous = self._previous_manifest(group)
            if previous.get('group') == group:
                self.manifests[group] = previous
        files = dict(previous.get('files', {}))
        changed = False
        for _, path, relative in self._walk(group, root):
            record = self._sync_file(path, files.get(relative), force)
            if record is not None and files.get(relative) != record:
                files[relative] = record
                changed = True
        if not changed and group in self.manifests:
            return False
        manifest = {'version': 1, 'group': group, 'updated_at': _now(), 'files': files}
        body = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode('utf-8')
        self.client.put_object(Bucket=self.bucket, Key=self._manifest_key(group), Body=body,
                               ContentLength=len(body), ContentType='application/json')
        self.manifests[group] = manifest
        self.uploaded.update(row['sha256'] for row in files.values())
        return True

    def sync(self, force=False):
        with _storage_lock(self.data):
            # Reload remote manifests after acquiring the cross-process writer lock.
            self.manifests.clear()
            requests = list((self.data / 'ui-state' / 'run-sync-requests').glob('*.json'))
            result = self._sync_locked(force or bool(requests))
            for request in requests:
                request.unlink(missing_ok=True)
            return result

    def _sync_locked(self, force):
        from .session_checkpoint import credential_values
        from runner.runtime import config_root
        self.checkpoint_secrets = credential_values(config_root(ROOT))
        self.checkpoint_errors = 0
        changed = 0
        from runner.diagnostics import phase
        with phase('backup.queue_snapshot'):
            snapshot = self._queue_snapshot()
        try:
            for group, root in self._groups():
                with phase('backup.group', group=group):
                    changed += bool(self._commit_group(group, root, force))
            if snapshot:
                with phase('backup.queue_commit'):
                    changed += bool(self._commit_group('state/queue', snapshot, True))
        finally:
            if snapshot:
                shutil.rmtree(snapshot.parent, ignore_errors=True)
        self._write_status({'enabled': True, 'status': 'warning' if self.checkpoint_errors else 'ready',
                            'message': '部分 LCR 会话检查点损坏，答卷和日志已同步；受影响案例可能需要重跑。' if self.checkpoint_errors else '',
                            'last_sync_at': _now(), 'groups': len(self.manifests), 'updated_groups': changed})
        return changed

    def restore(self):
        with _storage_lock(self.data):
            return self._restore_locked()

    def _restore_locked(self):
        paginator = self.client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.manifest_prefix):
            for item in page.get('Contents', []):
                self._restore_manifest(item.get('Key', ''))
        self._write_status({'enabled': True, 'status': 'ready', 'last_sync_at': _now(),
                            'groups': len(self.manifests), 'updated_groups': 0,
                            'preserved_local_files': self.preserved_files})
        return len(self.manifests)

    def _restore_manifest(self, key):
        if not key.startswith(self.manifest_prefix) or not key.endswith('.json'):
            return
        group = key[len(self.manifest_prefix):-5]
        if not self._safe_group(group):
            raise RuntimeError('COS 运行数据前缀中存在无效清单')
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        manifest = json.loads(response['Body'].read().decode('utf-8'))
        if manifest.get('version') != 1 or manifest.get('group') != group or not isinstance(manifest.get('files'), dict):
            raise RuntimeError('COS 运行数据清单格式无效')
        self.manifests[group] = manifest
        root = self._local_group(group)
        for relative, record in manifest['files'].items():
            self._restore_file(root, relative, record)
            self.uploaded.add(record['sha256'])

    def _local_group(self, group):
        parts = group.split('/')
        if parts[0] == 'runs':
            return self.data / 'runs' / Path(*parts[1:])
        if group == 'state/lcr-sessions':
            return self.data / 'home/.leancodereview/sessions'
        if parts[0] == 'state':
            return self.data / 'ui-state'
        return self.data / 'ui-state/jobs'

    def _queue_snapshot(self):
        source = self.data / 'ui-state' / 'queue.sqlite3'
        if not source.is_file():
            return None
        import sqlite3
        folder = Path(tempfile.mkdtemp(prefix='.run-storage-queue-', dir=source.parent))
        target = folder / 'queue.sqlite3'
        database = None
        snapshot = None
        success = False
        try:
            database = sqlite3.connect(source, timeout=15)
            snapshot = sqlite3.connect(target)
            database.backup(snapshot)
            success = True
            return target
        except Exception:
            raise RuntimeError('无法生成安全的队列数据库快照') from None
        finally:
            if snapshot:
                snapshot.close()
            if database:
                database.close()
            if not success:
                shutil.rmtree(folder, ignore_errors=True)

    def _restore_file(self, root, relative, record):
        pure = self._safe_relative_path(relative)
        digest, size = self._checked_record(record)
        target = root.joinpath(*pure.parts)
        if not target.resolve().is_relative_to(root.resolve()):
            raise RuntimeError('COS 运行数据路径越界')
        if self._file_matches(target, digest, size):
            return
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_file():
                raise RuntimeError('本地运行数据路径包含链接或非普通文件，已保留并停止恢复')
            # Local files may include changes made after the last COS snapshot.
            # Preserve them, including the SQLite database and its WAL state.
            self.preserved_files += 1
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
        response = self.client.get_object(Bucket=self.bucket, Key=self._object_key(digest))
        actual = hashlib.sha256()
        count = 0
        try:
            with temp.open('wb') as output:
                for chunk in response['Body'].iter_chunks(chunk_size=1024 * 1024):
                    if chunk:
                        count += len(chunk)
                        actual.update(chunk)
                        output.write(chunk)
            if count != size or actual.hexdigest() != digest:
                raise RuntimeError('COS 运行数据文件校验失败')
            temp.replace(target)
        except Exception:
            temp.unlink(missing_ok=True)
            raise

    @staticmethod
    def _safe_relative_path(relative):
        pure = PurePosixPath(relative)
        unsafe = pure.is_absolute() or not pure.parts or pure.name in SKIP_NAMES or pure.name.endswith('.tmp')
        if unsafe or any(part in {'', '.', '..', 'workspaces'} for part in pure.parts):
            raise RuntimeError('COS 运行数据文件路径无效')
        return pure

    @staticmethod
    def _checked_record(record):
        if not isinstance(record, dict):
            raise RuntimeError('COS 运行数据文件校验信息无效')
        digest, size = record.get('sha256'), record.get('size')
        if not isinstance(digest, str) or not SHA256.fullmatch(digest) or type(size) is not int or size < 0:
            raise RuntimeError('COS 运行数据文件校验信息无效')
        return digest, size

    @staticmethod
    def _file_matches(target, digest, size):
        return target.is_file() and not target.is_symlink() and target.stat().st_size == size and _digest(target) == digest

    def _write_status(self, status):
        target = self.data / 'ui-state' / 'run-storage-status.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
        temp.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(target)


class RunDataSynchronizer:
    def __init__(self, storage, interval=30):
        self.storage = storage
        self.interval = max(10, int(interval))
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, name='cos-run-data-sync', daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        deadline = time.monotonic() + self.interval
        requests = self.storage.data / 'ui-state/run-sync-requests'
        while not self.stopping.wait(1):
            requested = requests.exists() and next(requests.glob('*.json'), None) is not None
            if requested or time.monotonic() >= deadline:
                self.sync_once()
                deadline = time.monotonic() + self.interval

    def sync_once(self, force=False):
        try:
            from runner.diagnostics import phase
            with phase('backup.sync'):
                self.storage.sync(force=force)
        except Exception as error:
            from runner.diagnostics import event
            event('backup.failed', error_type=type(error).__name__)
            self.storage._write_status({'enabled': True, 'status': 'error', 'last_error_at': _now(),
                                        'message': 'COS 运行数据同步失败；本地数据仍在当前容器中，请勿重建容器'})
            print('COS 运行数据同步失败；本地数据仍在当前容器中，请勿重建容器。', flush=True)

    def stop(self):
        self.stopping.set()
        if self.thread.is_alive():
            self.thread.join(timeout=self.interval + 10)
        if not self.thread.is_alive():
            self.sync_once(force=True)


def restore_run_data(root=ROOT):
    mode = os.environ.get('BENCH_RUN_STORAGE_MODE', 'local').strip().lower()
    if mode not in {'local', 'cos'}:
        raise RuntimeError('BENCH_RUN_STORAGE_MODE 须为 local 或 cos')
    if mode != 'cos':
        return 0
    storage = RunDataStorage(root)
    count = storage.restore()
    print(f'已从 COS 恢复 {count} 组批次记录和任务日志。', flush=True)
    return count
