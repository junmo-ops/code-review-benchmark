"""Fail closed on S3 connections that ignore or reject atomic create-only PUTs."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import threading
import uuid


def _create(client, bucket, key, payload):
    try:
        response = client.put_object(Bucket=bucket, Key=key, Body=payload, ContentLength=len(payload),
                                     Metadata={'sha256': hashlib.sha256(payload).hexdigest()},
                                     ContentType='application/octet-stream', IfNoneMatch='*')
    except Exception as error:
        response = getattr(error, 'response', {})
        if (response.get('Error', {}).get('Code') == 'PreconditionFailed'
                and response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 412):
            return False
        raise RuntimeError('无法确认 S3 条件写返回的拒绝语义') from None
    if response.get('ResponseMetadata', {}).get('HTTPStatusCode') != 200:
        raise RuntimeError('S3 条件创建未返回已完成写入的 HTTP 200')
    return True


def _preserved(client, bucket, key, expected):
    response = client.get_object(Bucket=bucket, Key=key)
    body = response['Body']
    try:
        raw = body.read(len(expected) + 1)
    finally:
        body.close()
    if (raw != expected or response.get('ContentLength') != len(expected)
            or response.get('Metadata', {}).get('sha256') != hashlib.sha256(expected).hexdigest()):
        raise RuntimeError('S3 条件写未保留先写入的探测对象')


def _concurrent_create(client, bucket, key, payloads):
    start = threading.Barrier(2)

    def create(payload):
        start.wait(timeout=10)
        return _create(client, bucket, key, payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        return list(executor.map(create, payloads))


def require_atomic_create(client, bucket, prefix):
    root = prefix + '.write-probes/' + uuid.uuid4().hex + '/'
    serial, race = root + 'serial', root + 'race'
    payloads = (b'first-' + uuid.uuid4().hex.encode(), b'second-' + uuid.uuid4().hex.encode())
    try:
        if not _create(client, bucket, serial, payloads[0]):
            raise RuntimeError('S3 新探测对象已存在')
        if _create(client, bucket, serial, payloads[1]):
            raise RuntimeError('S3 服务忽略 If-None-Match，禁止写入 Skill 资产')
        _preserved(client, bucket, serial, payloads[0])
        outcomes = _concurrent_create(client, bucket, race, payloads)
        if outcomes.count(True) != 1:
            raise RuntimeError('S3 并发条件写不满足单一成功者契约')
        _preserved(client, bucket, race, payloads[outcomes.index(True)])
    except Exception:
        raise RuntimeError('Skill COS 原子条件写能力未确认；禁止保存或恢复 Skill，未退化为普通 PUT') from None
    finally:
        try:
            for key in (serial, race):
                client.delete_object(Bucket=bucket, Key=key)
        except Exception:
            raise RuntimeError('Skill COS 条件写探测对象清理失败；禁止写入 Skill，请检查专用前缀删除权限') from None
