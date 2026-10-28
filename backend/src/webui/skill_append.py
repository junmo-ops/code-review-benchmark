"""Append-only Skill publications for object stores without conditional PUTs."""
import hashlib
import io
import json
import re
import uuid

MAX_RECORD_BYTES = 4 * 1024**2
MAX_PUBLICATIONS = 10000
MAX_ORIGINALS = 1000
HEX = re.compile(r'^[a-f0-9]{64}$')
NONCE = re.compile(r'^[a-f0-9]{32}$')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')


def prefix(storage):
    return storage.skill_prefix + 'append-v2/'


def reference(root, kind, digest, size):
    leaf = 'archive.zip' if kind == 'archive' else kind + '.json'
    return dict(key=f'{root}{kind}/{digest}/{leaf}', sha256=digest, size=size)


def validate_reference(root, kind, value, maximum):
    if (not isinstance(value, dict) or set(value) != {'key', 'sha256', 'size'}
            or not isinstance(value['sha256'], str) or not HEX.fullmatch(value['sha256'])
            or type(value['size']) is not int or not 0 < value['size'] <= maximum
            or value != reference(root, kind, value['sha256'], value['size'])):
        raise RuntimeError('Skill COS 追加对象引用无效')
    return value


def check_head(storage, ref):
    head = storage._head(ref['key'])
    if (head is None or head.get('ContentLength') != ref['size']
            or head.get('Metadata', {}).get('sha256') != ref['sha256']):
        raise RuntimeError('Skill COS 已发布对象缺失或摘要元数据不匹配')


def read_verified(storage, ref, *, collect=False, output=None):
    try:
        reply = storage.client.get_object(Bucket=storage.bucket, Key=ref['key'])
    except Exception:
        raise RuntimeError('Skill COS 已发布版本记录缺失、对象不可读或网络异常') from None
    body, total, checksum, chunks = reply['Body'], 0, hashlib.sha256(), []
    try:
        while chunk := body.read(1024 * 1024):
            total += len(chunk)
            if total > ref['size']:
                raise RuntimeError('Skill COS 追加对象超过声明大小')
            checksum.update(chunk)
            if collect:
                chunks.append(chunk)
            if output is not None:
                output.write(chunk)
    finally:
        body.close()
    if (total != ref['size'] or checksum.hexdigest() != ref['sha256']
            or reply.get('ContentLength') != total
            or reply.get('Metadata', {}).get('sha256') != ref['sha256']):
        raise RuntimeError('Skill COS 追加对象 SHA256 校验失败')
    return b''.join(chunks)


def put_new(storage, ref, stream, content_type):
    if storage._head(ref['key']) is not None:
        raise RuntimeError('Skill COS 追加事务对象已存在，拒绝覆盖')
    try:
        reply = storage.client.put_object(Bucket=storage.bucket, Key=ref['key'], Body=stream,
            ContentLength=ref['size'], Metadata={'sha256': ref['sha256']}, ContentType=content_type)
    except Exception:
        # A lost response may follow a completed PUT. Confirm these exact bytes, never replay it.
        reply = None
    if reply is not None and reply.get('ResponseMetadata', {}).get('HTTPStatusCode') != 200:
        raise RuntimeError('Skill COS 追加写入未返回完成状态 HTTP 200')
    try:
        read_verified(storage, ref)
    except Exception:
        raise RuntimeError('Skill 资源未能可靠保存到 COS；追加对象完整回读未通过，请重传同一 ZIP') from None


def _put_json(storage, root, kind, value):
    raw = encoded(value)
    if len(raw) > MAX_RECORD_BYTES:
        raise RuntimeError('Skill COS 版本记录超过恢复上限 4 MiB')
    ref = reference(root, kind, hashlib.sha256(raw).hexdigest(), len(raw))
    put_new(storage, ref, io.BytesIO(raw), 'application/json')
    return ref


def _put_archive(storage, root, path):
    from .case_storage import _sha256
    from .skill_package import limits
    digest, size = _sha256(path), path.stat().st_size
    if not 0 < size <= limits()['upload_max_bytes']:
        raise RuntimeError('Skill COS 原包大小无效')
    ref = reference(root, 'archive', digest, size)
    with path.open('rb') as stream:
        put_new(storage, ref, stream, 'application/zip')
    return ref


def publish(storage, path, manifest, kind, originals=()):
    from .case_storage import _sha256
    from .skill_assets import IDENTIFIER
    identifier = manifest['id']
    if kind not in {'version', 'original'} or not IDENTIFIER.fullmatch(identifier):
        raise RuntimeError('Skill COS 追加发布身份无效')
    digest, size = _sha256(path), path.stat().st_size
    if len(originals) > MAX_ORIGINALS:
        raise RuntimeError('Skill COS 单次追加原包数量超过上限')
    archive = dict(sha256=digest, size=size)
    value = dict(format='skill-assets-v1', manifest=manifest, archive=archive)
    if len(encoded(value)) > MAX_RECORD_BYTES:
        raise RuntimeError('Skill COS 版本记录超过恢复上限 4 MiB')
    root = prefix(storage) + identifier + '/' + uuid.uuid4().hex + '/'
    archive_ref = _put_archive(storage, root, path)
    retained = [_put_archive(storage, root, original) for original in originals]
    record_ref = _put_json(storage, root, 'record', value)
    commit = dict(format='skill-append-commit-v2', id=identifier, kind=kind,
                  record=record_ref, archive=archive_ref, originals=retained)
    return _put_json(storage, root, 'commit', commit)


def _commit_reference(storage, item):
    suffix = item['Key'][len(prefix(storage)):].split('/')
    if (len(suffix) != 5 or not HEX.fullmatch(suffix[0]) or not NONCE.fullmatch(suffix[1])
            or suffix[2] != 'commit' or not HEX.fullmatch(suffix[3]) or suffix[4] != 'commit.json'):
        raise RuntimeError('Skill COS 追加发布路径无效')
    root = prefix(storage) + '/'.join(suffix[:2]) + '/'
    ref = reference(root, 'commit', suffix[3], item['Size'])
    return suffix[0], root, validate_reference(root, 'commit', ref, MAX_RECORD_BYTES)


def _read_publication(storage, item):
    from .skill_package import limits
    identifier, root, ref = _commit_reference(storage, item)
    commit = json.loads(read_verified(storage, ref, collect=True))
    if (not isinstance(commit, dict) or set(commit) != {'format', 'id', 'kind', 'record', 'archive', 'originals'}
            or commit['format'] != 'skill-append-commit-v2' or commit['id'] != identifier
            or commit['kind'] not in {'version', 'original'}):
        raise RuntimeError('Skill COS 追加发布标记无效')
    record = validate_reference(root, 'record', commit['record'], MAX_RECORD_BYTES)
    archive = validate_reference(root, 'archive', commit['archive'], limits()['upload_max_bytes'])
    value = json.loads(read_verified(storage, record, collect=True))
    if (value['format'] != 'skill-assets-v1' or value['manifest']['id'] != identifier
            or value['archive'] != dict(sha256=archive['sha256'], size=archive['size'])):
        raise RuntimeError('Skill COS 追加版本记录与发布标记冲突')
    originals = _original_references(root, commit, archive)
    result = []
    for current in [archive, *originals]:
        check_head(storage, current)
        read_verified(storage, current)
        kind = commit['kind'] if current is archive else 'original'
        result.append(dict(id=identifier, kind=kind, value=value, archive=current, commit=ref))
    return result


def _original_references(root, commit, archive):
    from .skill_package import limits
    originals = commit['originals']
    if (not isinstance(originals, list) or len(originals) > MAX_ORIGINALS
            or (commit['kind'] == 'original' and originals)):
        raise RuntimeError('Skill COS 追加原包清单无效')
    seen = {archive['sha256']}
    for ref in originals:
        validate_reference(root, 'archive', ref, limits()['upload_max_bytes'])
        if ref['sha256'] in seen:
            raise RuntimeError('Skill COS 追加原包清单重复')
        seen.add(ref['sha256'])
    return originals


def catalog(storage):
    paginator = storage.client.get_paginator('list_objects_v2')
    result = []
    for page in paginator.paginate(Bucket=storage.bucket, Prefix=prefix(storage)):
        for item in page.get('Contents', []):
            if not item['Key'].endswith('/commit.json'):
                continue
            if len(result) >= MAX_PUBLICATIONS:
                raise RuntimeError('Skill COS 追加发布数量超过恢复上限')
            result.extend(_read_publication(storage, item))
    return result
