"""Filesystem S3 boundary substitute. Never opens network connections."""
import io
import json
from pathlib import Path
import threading

from botocore.exceptions import ClientError


class Body(io.BytesIO):
    def iter_chunks(self, chunk_size=1024 * 1024):
        while chunk := self.read(chunk_size):
            yield chunk


class LocalCOS:
    def __init__(self, root, *, ignore_conditions=False):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.put_keys = []
        self.ignore_conditions = ignore_conditions

    def control(self):
        path = self.root / 'fault.json'
        return json.loads(path.read_text()) if path.exists() else {}

    def _path(self, bucket, key):
        path = self.root / 'objects' / bucket / key
        if not path.resolve().is_relative_to((self.root / 'objects').resolve()):
            raise ValueError('Invalid local COS key')
        return path

    def _fault(self, operation, key):
        suffix = self.control().get(operation)
        if suffix and key.endswith(suffix):
            raise ClientError({'Error': {'Code': 'AccessDenied'}}, operation)

    def put_object(self, Bucket, Key, Body, Metadata=None, **kwargs):
        self._fault('put', Key)
        with self.lock:
            self.put_keys.append(Key)
            return self._put(Bucket, Key, Body, Metadata, kwargs)

    def _put(self, bucket, key, body, metadata, options):
        path = self._path(bucket, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.control().get('conflict') and key.endswith(self.control()['conflict']):
            path.write_bytes(b'concurrent immutable object')
            path.with_name(path.name + '.metadata').write_text('{}')
        if not self.ignore_conditions and options.get('IfNoneMatch') == '*' and path.exists():
            raise ClientError({'Error': {'Code': 'PreconditionFailed'},
                               'ResponseMetadata': {'HTTPStatusCode': 412}}, 'PutObject')
        path.write_bytes(body.read() if hasattr(body, 'read') else body)
        path.with_name(path.name + '.metadata').write_text(json.dumps(metadata or {}))
        if self.control().get('corrupt_after_put') and key.endswith(self.control()['corrupt_after_put']):
            path.write_bytes(b'concurrent immutable object')
        self._fault('put_response', key)
        return {'ResponseMetadata': {'HTTPStatusCode': 200}}

    def head_object(self, Bucket, Key):
        self._fault('head', Key)
        path = self._path(Bucket, Key)
        if not path.is_file():
            raise ClientError({'Error': {'Code': 'NoSuchKey'},
                               'ResponseMetadata': {'HTTPStatusCode': 404}}, 'HeadObject')
        size = path.stat().st_size
        metadata = json.loads(path.with_name(path.name + '.metadata').read_text())
        if self.control().get('head_size') and Key.endswith(self.control()['head_size']):
            size += 1
        return dict(ContentLength=size, Metadata=metadata)

    def get_object(self, Bucket, Key):
        self._fault('get', Key)
        head = self.head_object(Bucket, Key)
        data = self._path(Bucket, Key).read_bytes()
        if self.control().get('corrupt') and Key.endswith(self.control()['corrupt']):
            data = b'!' * len(data)
        return dict(head, Body=Body(data))

    def delete_object(self, Bucket, Key):
        self._fault('delete', Key)
        path = self._path(Bucket, Key)
        path.unlink(missing_ok=True)
        path.with_name(path.name + '.metadata').unlink(missing_ok=True)
        return {}

    def get_paginator(self, operation):
        if operation != 'list_objects_v2':
            raise ValueError(operation)
        return self

    def paginate(self, Bucket, Prefix):
        base = self.root / 'objects' / Bucket
        items = [dict(Key=p.relative_to(base).as_posix(), Size=p.stat().st_size)
                 for p in base.rglob('*') if p.is_file() and not p.name.endswith('.metadata')
                 and p.relative_to(base).as_posix().startswith(Prefix)]
        yield dict(Contents=items)
