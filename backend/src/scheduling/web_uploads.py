"""Resumable upload manifests in MySQL, immutable chunks in COS."""
import hashlib
from pathlib import Path
import tempfile
import time
import uuid
from application.files import now
from webui.resumable_uploads import CHUNK_SIZE, IDENTIFIER, KINDS, _validate_request


class SharedUploads:
    def __init__(self, catalog):
        self.catalog, self.state, self.store = catalog, catalog.state, catalog.store

    @staticmethod
    def key(identifier):
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
            raise ValueError('上传任务标识无效')
        return 'upload-' + identifier

    def read(self, identifier):
        row = self.state.get(self.key(identifier))
        if not row or row['value'].get('deleted'):
            raise ValueError('上传任务不存在或已清理')
        if row['value']['status'] == 'receiving' and row['value']['expires_at'] <= time.time():
            raise ValueError('上传已过期，请重新选择文件')
        return row

    def rows(self):
        return [row for row in self.state.listing('upload-')
                if not row['value'].get('deleted') and (row['value']['status'] in {'queued', 'running'}
                                                       or row['value']['expires_at'] > time.time())]

    def create(self, payload, *, tool=False):
        if tool:
            from repository_migration.store import validate_request
            name, size, fingerprint, main = validate_request(payload)
            kind = 'zip'
        else:
            name, size, kind, fingerprint = _validate_request(payload)
            main = ''
            if kind == 'skill':
                from webui.skill_package import limits
                if size > limits()['upload_max_bytes']:
                    raise ValueError('Skill ZIP 超过上传上限')
        identifier = uuid.uuid4().hex
        value = dict(id=identifier, kind=kind, filename=name, size=size, fingerprint=fingerprint,
                     main=main, status='receiving', offset=0, chunks=[], chunk_size=CHUNK_SIZE,
                     created_at=now(), updated_at=now(), expires_at=time.time() + 7 * 86400,
                     message='等待上传；刷新后重新选择同一文件可续传', completed=0, total=0)
        return self.view(self.state.admit(self.key(identifier), value))

    def view(self, value):
        result = {key: item for key, item in value.items()
                  if key not in {'chunks', 'output', 'deleted'}}
        if value['kind'] == 'zip':
            return result
        result['status'] = 'receiving' if value['status'] == 'receiving' else 'queued'
        if value['status'] in {'aborted', 'expired'}:
            result['status'] = value['status']
        elif value['status'] != 'receiving':
            result['task'] = self.task(value)
        return result

    @staticmethod
    def task(value):
        return {key: item for key, item in value.items()
                if key not in {'chunks', 'output', 'deleted'}}

    def current(self, kind):
        if kind not in KINDS:
            raise ValueError('上传类型无效')
        values = [row['value'] for row in self.rows()
                  if row['value']['kind'] == kind and row['value']['status'] == 'receiving']
        return self.view(max(values, key=lambda row: row['created_at'])) if values else None

    def import_status(self, identifier=None):
        if identifier:
            return self.task(self.read(identifier)['value'])
        values = [row['value'] for row in self.rows()
                  if row['value']['kind'] != 'zip' and row['value']['status'] not in {'receiving', 'aborted'}]
        return self.task(max(values, key=lambda row: row['created_at'])) if values else None

    def chunk(self, identifier, offset, stream, length):
        row = self.read(identifier)
        value = row['value']
        expected = min(CHUNK_SIZE, value['size'] - value['offset'])
        if value['status'] != 'receiving' or offset != value['offset'] or expected <= 0 or length != expected:
            raise ValueError('上传位置或大小不符，请刷新后选择同一 ZIP 续传')
        with tempfile.TemporaryDirectory(dir=self.catalog.app.data) as temporary:
            path = Path(temporary) / 'chunk'
            with path.open('wb') as target:
                remaining = expected
                while remaining:
                    data = stream.read(min(1024 * 1024, remaining))
                    if not data:
                        raise ValueError('上传中断，请续传当前分块')
                    target.write(data)
                    remaining -= len(data)
            record = self.store.put(path)
        value['chunks'].append(record)
        value.update(offset=offset + expected, updated_at=now(), expires_at=time.time() + 7 * 86400)
        self.state.put(self.key(identifier), value, row['revision'])
        return self.view(value)

    def complete(self, identifier):
        row = self.read(identifier)
        value = row['value']
        if value['status'] == 'receiving':
            if value['offset'] != value['size']:
                raise ValueError('文件尚未上传完整')
            value.update(status='queued', phase='queued', message='已接收，等待后台校验', updated_at=now())
            from .resources import submit
            submit(self.state.queue, self.state, self.key(identifier), value, row['revision'])
        elif value['status'] not in {'queued', 'running', 'succeeded', 'failed'}:
            raise ValueError('上传已取消或过期')
        return self.task(value)

    def abort(self, identifier, *, delete=False):
        row = self.read(identifier)
        value = row['value']
        if value['status'] in {'queued', 'running'}:
            raise ValueError('后台正在处理，请等待结束')
        value.update(status='aborted', chunks=[], deleted=delete, updated_at=now())
        value.pop('output', None)
        self.state.put(self.key(identifier), value, row['revision'])
        return self.view(value)

    def assemble(self, value, destination):
        size = 0
        with tempfile.TemporaryDirectory(dir=destination.parent) as temporary, destination.open('wb') as output:
            chunk = Path(temporary) / 'part'
            for record in value['chunks']:
                self.store.get(record, chunk)
                with chunk.open('rb') as source:
                    while data := source.read(1024 * 1024):
                        output.write(data)
                        size += len(data)
        if size != value['size']:
            raise ValueError('上传文件大小不匹配')

    def submit_file(self, archive, kind):
        path = Path(archive)
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
        # Git requests use JSON internally but follow the same durable transfer protocol.
        visible_kind = 'repository' if kind == 'repository_git' else kind
        row = self.create(dict(filename='resource.zip', size=path.stat().st_size,
                               fingerprint=digest.hexdigest(), kind=visible_kind))
        identifier = row['id']
        with path.open('rb') as source:
            for offset in range(0, row['size'], CHUNK_SIZE):
                self.chunk(identifier, offset, source, min(CHUNK_SIZE, row['size'] - offset))
        if kind == 'repository_git':
            stored = self.read(identifier)
            self.state.put(self.key(identifier), dict(stored['value'], kind=kind), stored['revision'])
        return dict(task=self.complete(identifier), message='资源包已保存，正在后台导入。')
