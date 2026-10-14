"""Durable, resumable ZIP uploads used before background resource imports."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import datetime
import zipfile

from runner.locking import acquire, release
from .case_upload import limits
from .service import now, save

CHUNK_SIZE = 8 * 1024 * 1024
IDENTIFIER = re.compile(r'^[a-f0-9]{32}$')
KINDS = {'dataset', 'repository'}


def _root(dashboard):
    path = dashboard.state / 'case-uploads'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cleanup_expired(root):
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
    for folder in root.iterdir():
        if not folder.is_dir() or folder.is_symlink() or not IDENTIFIER.fullmatch(folder.name):
            continue
        try:
            manifest = _manifest(folder)
            updated = datetime.datetime.fromisoformat(manifest['updated_at'])
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=datetime.timezone.utc)
            if updated >= cutoff or manifest['status'] == 'queued':
                continue
            expired = False
            with (folder / 'lock').open('a+b') as lock:
                try:
                    acquire(lock)
                except BlockingIOError:
                    continue
                try:
                    manifest.update(status='expired', updated_at=now())
                    save(folder / 'manifest.json', manifest)
                    expired = True
                finally:
                    release(lock)
            if expired:
                shutil.rmtree(folder, ignore_errors=True)
        except (OSError, ValueError, KeyError, TypeError):
            continue


def _folder(dashboard, identifier):
    if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
        raise ValueError('上传任务标识无效')
    root = _root(dashboard).resolve()
    folder = root / identifier
    if folder.resolve() != folder or not folder.resolve().is_relative_to(root):
        raise ValueError('上传任务路径无效')
    return folder


def _manifest(folder):
    path = folder / 'manifest.json'
    if not path.is_file() or path.is_symlink():
        raise ValueError('上传任务不存在或已过期')
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('id') != folder.name or value.get('kind') not in KINDS:
        raise ValueError('上传任务记录无效')
    return value


@contextmanager
def _locked(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'lock').open('a+b') as lock:
        try:
            acquire(lock)
        except BlockingIOError:
            raise ValueError('该上传任务正在处理，请稍后重试') from None
        try:
            yield
        finally:
            release(lock)


def _committed_chunks(folder, manifest):
    chunks = folder / 'chunks'
    chunks.mkdir(exist_ok=True)
    count = (manifest['size'] + CHUNK_SIZE - 1) // CHUNK_SIZE
    offset = 0
    for index in range(count):
        path = chunks / f'{index:08d}.part'
        expected = min(CHUNK_SIZE, manifest['size'] - offset)
        if not path.is_file() or path.is_symlink() or path.stat().st_size != expected:
            break
        offset += expected
    return offset, offset // CHUNK_SIZE


def _view(folder, manifest):
    offset = manifest['size'] if manifest['status'] == 'queued' else _committed_chunks(folder, manifest)[0]
    value = {key: manifest[key] for key in ('id', 'kind', 'filename', 'size', 'fingerprint', 'status', 'created_at', 'updated_at')}
    value.update(offset=offset, chunk_size=CHUNK_SIZE)
    task = _linked_task(folder, manifest)
    if task:
        value['task'] = task
    return value


def _linked_task(folder, manifest):
    task = manifest.get('task')
    try:
        current = json.loads((folder.parent.parent / 'case-imports/current.json').read_text(encoding='utf-8'))
        if current.get('upload_id') == manifest['id']:
            return current
    except (OSError, ValueError, AttributeError):
        pass  # No current import is available; retain the saved task snapshot.
    try:
        identifier = task.get('id') if isinstance(task, dict) else None
        if identifier and IDENTIFIER.fullmatch(identifier):
            return json.loads((folder.parent.parent / 'case-imports' / (identifier + '.json')).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass  # Older installations did not retain individual task records.
    return task


def _validate_filename(name):
    if not isinstance(name, str) or not name or name != Path(name).name:
        raise ValueError('ZIP 名称无效')
    if '/' in name or '\\' in name:
        raise ValueError('ZIP 名称无效')
    if not name.lower().endswith('.zip'):
        raise ValueError('请选择 ZIP 文件上传')
    return name


def _validate_size(size):
    maximum = limits()
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0 or size > maximum['upload_max_bytes']:
        raise ValueError('ZIP 大小无效或超过 ' + limits()['upload_max_label'])
    return size


def _validate_request(request):
    if not isinstance(request, dict):
        raise ValueError('上传参数无效')
    name = _validate_filename(request.get('filename'))
    size = _validate_size(request.get('size'))
    kind = request.get('kind')
    fingerprint = request.get('fingerprint')
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError('上传类型无效')
    if not isinstance(fingerprint, str) or not re.fullmatch(r'[a-f0-9]{64}', fingerprint):
        raise ValueError('上传文件标识无效')
    return name, size, kind, fingerprint


def create(dashboard, request):
    name, size, kind, fingerprint = _validate_request(request)
    lock_path = dashboard.state / 'case-upload.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+b') as lock:
        try:
            acquire(lock)
        except BlockingIOError:
            raise ValueError('已有案例资源操作正在处理，请稍后重试') from None
        try:
            with dashboard.lock:
                return _create_locked(dashboard, name, size, kind, fingerprint)
        finally:
            release(lock)


def _create_locked(dashboard, name, size, kind, fingerprint):
    from .case_import import ensure_idle
    ensure_idle(dashboard)
    root = _root(dashboard)
    _cleanup_expired(root)
    if any(current(dashboard, upload_kind)['upload'] for upload_kind in KINDS):
        raise ValueError('已有未完成上传，请先续传或放弃当前 ZIP')
    import uuid
    identifier = uuid.uuid4().hex
    folder = root / identifier
    folder.mkdir()
    value = dict(id=identifier, kind=kind, filename=name, size=size, fingerprint=fingerprint,
                 status='receiving', created_at=now(), updated_at=now())
    save(folder / 'manifest.json', value)
    return {'upload': _view(folder, value)}


def status(dashboard, identifier):
    folder = _folder(dashboard, identifier)
    _cleanup_expired(folder.parent)
    value = _manifest(folder)
    return {'upload': _view(folder, value)}


def current(dashboard, kind):
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError('上传类型无效')
    candidates = []
    root = _root(dashboard)
    _cleanup_expired(root)
    for folder in root.iterdir():
        if not folder.is_dir() or folder.is_symlink() or not IDENTIFIER.fullmatch(folder.name):
            continue
        try:
            value = _manifest(folder)
            if value['kind'] == kind and value['status'] == 'receiving':
                candidates.append((value.get('updated_at', ''), _view(folder, value)))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if not candidates:
        return {'upload': None}
    return {'upload': max(candidates, key=lambda item: item[0])[1]}


def put_chunk(dashboard, identifier, index, stream, length):
    folder = _folder(dashboard, identifier)
    with _locked(folder):
        value = _manifest(folder)
        if value['status'] != 'receiving':
            raise ValueError('该上传任务已结束')
        offset, expected_index = _committed_chunks(folder, value)
        if not isinstance(index, int) or index != expected_index:
            raise ValueError(f'上传分块位置不匹配，请从第 {expected_index + 1} 块继续')
        expected = min(CHUNK_SIZE, value['size'] - offset)
        if expected <= 0 or length != expected:
            raise ValueError('上传分块大小不正确，请重试')
        chunks = folder / 'chunks'
        for temporary_chunk in chunks.glob('.chunk-*'):
            temporary_chunk.unlink(missing_ok=True)
        destination = chunks / f'{index:08d}.part'
        fd, temporary = tempfile.mkstemp(prefix='.chunk-', dir=chunks)
        digest = hashlib.sha256()
        received = 0
        try:
            with os.fdopen(fd, 'wb') as output:
                while received < expected:
                    data = stream.read(min(1024 * 1024, expected - received))
                    if not data:
                        raise ValueError('分块传输中断，请重试当前分块')
                    received += len(data)
                    digest.update(data)
                    output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
        value.update(updated_at=now(), last_chunk_sha256=digest.hexdigest())
        save(folder / 'manifest.json', value)
        return {'upload': _view(folder, value)}


def complete(dashboard, identifier):
    folder = _folder(dashboard, identifier)
    with _locked(folder):
        value = _manifest(folder)
        from .case_import import current, submit
        existing = current(dashboard)
        if existing and existing.get('upload_id') == identifier:
            value.update(status='queued', updated_at=now(), task=existing)
            save(folder / 'manifest.json', value)
            return {'task': existing, 'message': '资源包已接收，正在后台导入。'}
        if value['status'] == 'queued' and value.get('task'):
            return {'task': _linked_task(folder, value), 'message': '资源包已接收，请查看导入结果。'}
        if value['status'] != 'receiving':
            raise ValueError('该上传任务已结束，请重新选择 ZIP 开始上传')
        offset, _ = _committed_chunks(folder, value)
        if offset != value['size']:
            raise ValueError('ZIP 尚未完整上传，不能开始导入')
        archive = folder / 'complete.zip'
        digest = hashlib.sha256()
        with archive.open('wb') as output:
            for index in range((value['size'] + CHUNK_SIZE - 1) // CHUNK_SIZE):
                part = folder / 'chunks' / f'{index:08d}.part'
                with part.open('rb') as source:
                    while data := source.read(1024 * 1024):
                        output.write(data)
                        digest.update(data)
            output.flush()
            os.fsync(output.fileno())
        if archive.stat().st_size != value['size'] or not zipfile.is_zipfile(archive):
            value.update(status='failed', message='上传内容不是有效 ZIP 文件', updated_at=now())
            save(folder / 'manifest.json', value)
            archive.unlink(missing_ok=True)
            shutil.rmtree(folder / 'chunks', ignore_errors=True)
            raise ValueError(value['message'])
        result = submit(dashboard, archive, kind=value['kind'], upload_id=identifier)
        task = result['task']
        value.update(status='queued', task=task, sha256=digest.hexdigest(), updated_at=now())
        save(folder / 'manifest.json', value)
        shutil.rmtree(folder / 'chunks', ignore_errors=True)
        return result


def abort(dashboard, identifier):
    folder = _folder(dashboard, identifier)
    with _locked(folder):
        value = _manifest(folder)
        if value['status'] == 'queued':
            raise ValueError('后台已开始导入，不能取消上传')
        shutil.rmtree(folder / 'chunks', ignore_errors=True)
        (folder / 'complete.zip').unlink(missing_ok=True)
        value.update(status='aborted', updated_at=now())
        save(folder / 'manifest.json', value)
        return {'upload': _view(folder, value)}
