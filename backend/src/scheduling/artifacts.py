"""Immutable content-addressed files; SQL pointers, never a mutable run manifest, publish them."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from .contracts import identifier

SKIP = {'workspaces', '.git', '.cloud.lock', 'stop-request.json', '__pycache__'}
MAX_FILE = 1024 * 1024 * 1024
MAX_TOTAL = 4 * MAX_FILE


def safe_path(name):
    path = PurePosixPath(name)
    if (not name or '\\' in name or path.is_absolute() or ':' in name
            or any(part in {'.', '..', ''} for part in name.split('/'))):
        raise ValueError('集群证据文件路径无效')
    return path


class Artifacts:
    def __init__(self, scope, storage=None, *, max_file=MAX_FILE):
        from webui.case_storage import CaseArchiveStorage
        storage = storage or CaseArchiveStorage()
        self.client, self.bucket = storage.client, storage.bucket
        self.max_file = max_file
        self.prefix = storage.run_prefix.rstrip('/') + '/cluster/' + identifier(scope, 64) + '/'

    def _key(self, digest):
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('集群对象摘要无效')
        return self.prefix + digest[:2] + '/' + digest

    def put(self, path):
        # Upload a stable private snapshot, never a file being changed by a child process.
        digest = hashlib.sha256()
        size = 0
        with tempfile.TemporaryFile() as snapshot:
            with Path(path).open('rb') as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    size += len(chunk)
                    if size > self.max_file:
                        raise ValueError('单个集群文件超过存储上限')
                    digest.update(chunk)
                    snapshot.write(chunk)
            sha = digest.hexdigest()
            snapshot.seek(0)
            # Cleanup can remove an unreferenced object cached by a long-lived process.
            # Always persist before publishing a new pointer, including after maintenance.
            self.client.put_object(Bucket=self.bucket, Key=self._key(sha), Body=snapshot,
                                   ContentLength=size, Metadata={'sha256': sha})
        return {'sha256': sha, 'size': size}

    def get(self, record, destination):
        if type(record.get('size')) is not int or not 0 <= record['size'] <= self.max_file:
            raise ValueError('集群对象大小无效')
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        response = self.client.get_object(Bucket=self.bucket, Key=self._key(record['sha256']))
        digest, size = hashlib.sha256(), 0
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                temporary = Path(output.name)
                for chunk in iter(lambda: response['Body'].read(1024 * 1024), b''):
                    size += len(chunk)
                    if size > record['size']:
                        raise ValueError('集群对象大小不匹配')
                    digest.update(chunk)
                    output.write(chunk)
            if size != record['size'] or digest.hexdigest() != record['sha256']:
                raise ValueError('集群对象摘要不匹配')
            temporary.replace(target)
        finally:
            response['Body'].close()
            if temporary:
                temporary.unlink(missing_ok=True)

    def capture(self, root):
        root = Path(root)
        files, total = {}, 0
        for path in _files(root):
            relative = path.relative_to(root)
            if set(relative.parts) & SKIP or path.name.endswith(('.tmp', '.lock')):
                continue
            if path.is_symlink():
                raise ValueError('集群证据不能包含符号链接')
            if path.is_file():
                total += path.stat().st_size
                if total > MAX_TOTAL or len(files) >= 20000:
                    raise ValueError('集群证据超过大小或文件数上限')
                safe_path(relative.as_posix())
                files[relative.as_posix()] = self.put(path)
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / 'manifest.json'
            manifest.write_text(json.dumps(files, ensure_ascii=False), encoding='utf-8')
            return self.put(manifest)

    def restore(self, record, root, *, only=None):
        root = Path(root).resolve()
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / 'manifest.json'
            self.get(record, manifest)
            files = json.loads(manifest.read_text(encoding='utf-8'))
        if not isinstance(files, dict) or len(files) > 20000:
            raise ValueError('集群清单无效')
        total = 0
        for name, item in files.items():
            relative = safe_path(name)
            total += item['size']
            target = root.joinpath(*relative.parts)
            if total > MAX_TOTAL or not target.resolve().is_relative_to(root):
                raise ValueError('集群证据超限或路径越界')
            if only is None or name in only:
                self.get(item, target)


def _files(root):
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = [name for name in folders if name not in SKIP]
        if any((Path(directory) / name).is_symlink() for name in folders):
            raise ValueError('集群输入不能包含目录符号链接')
        for name in sorted(files):
            yield Path(directory) / name
