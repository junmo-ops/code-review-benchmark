"""Conservative reachability for current-scope immutable cluster objects only."""
import hashlib
import json
import re
import time
import uuid
from .web_state import WebState

DIGEST = re.compile(rb'[a-f0-9]{64}')
TABLES = ('bench_scheduler_tasks', 'bench_scheduler_attempts', 'bench_scheduler_runs', 'bench_web_state')


def roots(queue):
    found = set()
    with queue.database.transaction() as db:
        for table in TABLES:
            db.execute('SELECT * FROM ' + table + ' WHERE scope=%s', (queue.scope,))
            for row in db.fetchall():
                if table == 'bench_web_state' and row['name'].startswith('cos-'):
                    continue
                found.update(DIGEST.findall(json.dumps(row, default=str).encode()))
    return {value.decode() for value in found}



def _snapshots(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return set()
    if isinstance(value, list):
        return set().union(*(_snapshots(item) for item in value))
    if not isinstance(value, dict):
        return set()
    found = set()
    for key, item in value.items():
        if key in {'snapshot', 'input'} and isinstance(item, dict) and 'sha256' in item:
            found.add(item['sha256'])
        found.update(_snapshots(item))
    return found


def require_snapshots(queue, inventory):
    with queue.database.transaction() as db:
        for table in TABLES:
            db.execute('SELECT * FROM ' + table + ' WHERE scope=%s', (queue.scope,))
            for row in db.fetchall():
                if table == 'bench_web_state' and (row['name'] == 'models' or row['name'].startswith('cos-')):
                    continue
                if _snapshots(row) - inventory.keys():
                    raise ValueError('数据库引用的快照缺失，无法完整追踪引用；请先修复存储，不执行清理')


def objects(store):
    result = {}
    for page in store.client.get_paginator('list_objects_v2').paginate(Bucket=store.bucket, Prefix=store.prefix):
        for row in page.get('Contents', []):
            digest = row['Key'].rsplit('/', 1)[-1]
            if re.fullmatch('[a-f0-9]{64}', digest) and row['Key'] == store._key(digest):
                result[digest] = row
            if len(result) > 100000:
                raise ValueError('对象超过 10 万，本次扫描停止，未生成删除清单')
    return result


def references(store, row):
    response = store.client.get_object(Bucket=store.bucket, Key=row['Key'], IfMatch=row['ETag'])
    body = response['Body']
    found, tail, digest = set(), b'', hashlib.sha256()
    try:
        while True:
            chunk = body.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            found.update(DIGEST.findall(tail + chunk))
            tail = (tail + chunk)[-63:]
    finally:
        body.close()
    if digest.hexdigest() != row['Key'].rsplit('/', 1)[-1]:
        raise ValueError('引用对象摘要不一致，停止清理')
    return {value.decode() for value in found}


def candidates(queue, store):
    versioning = store.client.get_bucket_versioning(Bucket=store.bucket)
    if versioning.get('Status'):
        raise ValueError('桶已开启或暂停版本控制，本功能不删除历史版本，也不把删除标记计为释放容量')
    inventory = objects(store)
    require_snapshots(queue, inventory)
    pending, marked = list(roots(queue)), set()
    while pending:
        digest = pending.pop()
        if digest in marked:
            continue
        marked.add(digest)
        if digest in inventory:
            pending.extend(references(store, inventory[digest]) - marked)
    cutoff = time.time() - 86400
    result = []
    for digest, row in inventory.items():
        if digest not in marked and row['LastModified'].timestamp() < cutoff:
            result.append({'key': row['Key'], 'size': row['Size'], 'etag': row['ETag']})
    result.sort(key=lambda row: (-row['size'], row['key']))
    return result[:1000]


def save_preview(queue, store):
    items = candidates(queue, store)
    value = {'id': uuid.uuid4().hex, 'created': time.time(), 'items': items,
             'bytes': sum(row['size'] for row in items), 'prefix': store.prefix, 'bucket': store.bucket}
    state = WebState(queue)
    previous = state.get('cos-preview')
    state.put('cos-preview', value, previous['revision'] if previous else 0)
    return value


def delete_preview(queue, store, identifier):
    state = WebState(queue)
    row = state.get('cos-preview')
    if not row or row['value']['id'] != identifier:
        raise ValueError('清理预览已失效，请重新扫描')
    preview = row['value']
    if time.time() - preview['created'] > 900 or (preview['prefix'], preview['bucket']) != (store.prefix, store.bucket):
        raise ValueError('预览已过期或存储配置已改变，请重新扫描')
    current = {item['key']: item for item in candidates(queue, store)}
    selected = [item for item in preview['items'] if current.get(item['key']) == item]
    if len(selected) != len(preview['items']):
        raise ValueError('对象或引用发生变化，未执行删除，请重新扫描')
    # Consume before deletion: retries must not replay a stale plan.
    state.put('cos-preview', dict(preview, id='consumed'), row['revision'])
    result = {'id': identifier, 'deleted': [], 'failed': [], 'bytes': 0}
    for item in selected:
        try:
            store.client.delete_object(Bucket=store.bucket, Key=item['key'], IfMatch=item['etag'])
            result['deleted'].append(item['key'])
            result['bytes'] += item['size']
        except Exception:
            result['failed'].append(item['key'])
            break
    result['remaining'] = len(selected) - len(result['deleted'])
    state.put('cos-audit-' + identifier, result)
    return result
