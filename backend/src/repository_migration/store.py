"""Local, bounded temporary storage for the independent ZIP tool."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid

from runner.locking import acquire, release

CHUNK = 8 * 1024 * 1024
TTL = 24 * 3600
ACTIVE = {'receiving', 'queued', 'running'}


def limits():
    return {'max_bytes': 2 * 1024 ** 3, 'chunk_size': CHUNK, 'retention_hours': 24}


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        try:
            acquire(handle)
        except BlockingIOError:
            raise ValueError('文件正在处理，请稍后重试') from None
        try:
            yield
        finally:
            release(handle)


def save(folder, value):
    fd, name = tempfile.mkstemp(prefix='state-', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, folder / 'state.json')
    finally:
        Path(name).unlink(missing_ok=True)


def read(folder):
    path = folder / 'state.json'
    if not path.is_file():
        raise ValueError('处理记录不存在或已过期')
    return json.loads(path.read_text(encoding='utf-8'))


def folder_for(root, identifier):
    if not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise ValueError('处理记录编号无效')
    folder = root / identifier
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError('处理记录不存在或已过期')
    return folder


def listing(root):
    root.mkdir(parents=True, exist_ok=True)
    result = []
    for folder in root.iterdir():
        if not re.fullmatch(r'[a-f0-9]{32}', folder.name) or folder.is_symlink():
            continue
        if not (folder / 'state.json').is_file():
            continue
        result.append(read(folder))
    return sorted(result, key=lambda row: row['created_at'], reverse=True)


def cleanup(root):
    for row in listing(root):
        if row['status'] in {'queued', 'running'} or row['expires_at'] > time.time():
            continue
        folder = root / row['id']
        try:
            with locked(folder / 'lock'):
                if read(folder)['expires_at'] > time.time():
                    continue
                for child in folder.iterdir():
                    if child.name == 'lock':
                        continue
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
            (folder / 'lock').unlink(missing_ok=True)
            folder.rmdir()
        except (OSError, ValueError):
            continue  # A concurrent upload/download retains its files until the next sweep.


def validate_request(payload):
    if not isinstance(payload, dict):
        raise ValueError('上传参数无效')
    name, size = payload.get('filename'), payload.get('size')
    if not isinstance(name, str) or len(name) > 200 or not name.lower().endswith('.zip'):
        raise ValueError('请选择 ZIP 文件')
    if re.search(r'[/\\\x00-\x1f]', name):
        raise ValueError('ZIP 文件名称无效')
    if type(size) is not int or not 0 < size <= limits()['max_bytes']:
        raise ValueError('ZIP 文件大小须在 0–2 GiB 之间')
    fingerprint = payload.get('fingerprint', '')
    if not isinstance(fingerprint, str) or not re.fullmatch(r'[a-f0-9]{64}', fingerprint):
        raise ValueError('文件标识无效')
    main = payload.get('main', '')
    if not isinstance(main, str) or (main and not re.fullmatch(r'[A-Za-z0-9_]{1,100}', main)):
        raise ValueError('主分支仅支持字母、数字、下划线')
    return name, size, fingerprint, main


def create(root, payload):
    name, size, fingerprint, main = validate_request(payload)
    with locked(root / 'admission.lock'):
        cleanup(root)
        rows = listing(root)
        for row in rows:
            if row['status'] == 'receiving' and row['fingerprint'] == fingerprint and row['main'] == main:
                return row
        if len(rows) >= 30 or sum(row['status'] in ACTIVE for row in rows) >= 5:
            raise ValueError('最多保留 30 条记录、同时处理 5 个文件，请先下载并清理已有记录')
        if shutil.disk_usage(root).free < size * 2 + 256 * 1024 ** 2:
            raise ValueError('服务器临时磁盘空间不足')
        identifier = uuid.uuid4().hex
        folder = root / identifier
        folder.mkdir()
        row = dict(id=identifier, filename=name, size=size, fingerprint=fingerprint, main=main,
                   status='receiving', message='等待上传；刷新后重新选择同一文件可续传', offset=0,
                   created_at=time.time(), expires_at=time.time() + TTL)
        save(folder, row)
        return row


def put_chunk(root, identifier, offset, stream, length):
    folder = folder_for(root, identifier)
    with locked(folder / 'lock'):
        row = read(folder)
        expected = min(CHUNK, row['size'] - row['offset'])
        if row['status'] != 'receiving' or offset != row['offset'] or expected <= 0 or length != expected:
            raise ValueError('上传位置或大小不符，请重新选择同一 ZIP 续传')
        path = folder / 'input.zip'
        with path.open('r+b' if path.exists() else 'w+b') as output:
            output.truncate(offset)
            output.seek(offset)
            remaining = expected
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError('上传已中断，请重新选择同一 ZIP 续传')
                output.write(chunk)
                remaining -= len(chunk)
            output.flush()
            os.fsync(output.fileno())
        row.update(offset=offset + expected, expires_at=time.time() + TTL)
        save(folder, row)
        return row


def complete(root, identifier):
    folder = folder_for(root, identifier)
    with locked(folder / 'lock'):
        row = read(folder)
        if row['status'] in {'queued', 'running', 'succeeded'}:
            return row
        if row['status'] != 'receiving' or row['offset'] != row['size']:
            raise ValueError('ZIP 尚未上传完整')
        row.update(status='queued', message='等待处理', expires_at=time.time() + TTL)
        save(folder, row)
        return row


def remove(root, identifier):
    folder = folder_for(root, identifier)
    with locked(folder / 'lock'):
        row = read(folder)
        if row['status'] in {'queued', 'running'}:
            raise ValueError('后台正在处理，完成后可清理')
        row['expires_at'] = 0
        save(folder, row)
    cleanup(root)
