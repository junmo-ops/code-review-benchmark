"""Read-only, bounded inventory of configured project COS prefixes."""
from flask import jsonify, request
from .case_storage import CaseArchiveStorage


def scopes(storage):
    parent = storage.prefix.rsplit('/', 1)[0]
    return {'resources': storage.prefix + '/', 'runs': storage.run_prefix + '/',
            'models': parent + '/model-configs/'}


def category(key, storage):
    prefixes = [
        (storage.run_prefix + '/cluster/', '集群执行证据', '需核对共享数据库引用，不能按旧版 manifest 判断'),
        (storage.run_prefix + '/objects/', '运行文件快照', '需核对全部运行清单及正在上传的文件'),
        (storage.run_prefix + '/manifests/', '运行恢复清单', '恢复入口，保留'),
        (storage.prefix + '/v1/archives/', '旧版完整案例包', '确认迁移完成且无需旧包恢复后再评估'),
        (storage.prefix + '/v2/skills/', 'Skill 资源', '需核对 Skill 版本和测评批次引用'),
        (storage.prefix + '/v2/', '案例与仓库资源', '通过案例集管理检查资源引用'),
    ]
    for prefix, label, advice in prefixes:
        if key.startswith(prefix):
            return label, advice
    return '配置或其他对象', '尚未核对引用，不可据此认定可删除'


def inventory_page(storage, scope, cursor):
    prefixes = scopes(storage)
    if scope not in prefixes or len(cursor) > 8192:
        raise ValueError('COS 扫描范围或分页参数无效')
    options = {'Bucket': storage.bucket, 'Prefix': prefixes[scope], 'MaxKeys': 500}
    if cursor:
        options['ContinuationToken'] = cursor
    try:
        page = storage.client.list_objects_v2(**options)
    except Exception:
        raise ValueError('COS 列举失败，请检查服务端连接和 ListBucket 权限；请重新扫描') from None
    rows = []
    for item in page.get('Contents', []):
        key = item['Key']
        if not key.startswith(prefixes[scope]):
            raise ValueError('COS 返回了扫描范围以外的对象，已停止')
        label, advice = category(key, storage)
        modified = item.get('LastModified')
        rows.append({'key': key, 'size': int(item['Size']), 'modified': modified.isoformat() if modified else '',
                     'category': label, 'advice': advice})
    cursor = page.get('NextContinuationToken', '') if page.get('IsTruncated') else ''
    if page.get('IsTruncated') and not cursor:
        raise ValueError('COS 分页响应不完整，不能认定扫描完成')
    return {'items': rows, 'cursor': cursor, 'prefix': prefixes[scope], 'bucket': storage.bucket}


def register(app):
    @app.get('/api/cos/inventory')
    def inventory():
        try:
            storage = CaseArchiveStorage()
        except RuntimeError:
            return jsonify(error='COS 未就绪，请检查服务端存储配置与 Secret；无需在页面输入密钥'), 503
        return jsonify(inventory_page(storage, request.args.get('scope', 'runs'),
                                      request.args.get('cursor', '')))
