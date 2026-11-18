"""Cluster resource HTTP adapters, retaining the existing browser API contract."""
from pathlib import Path
import secrets
import tempfile
import time
from urllib.parse import urlencode
from flask import g, jsonify, request, send_file
from webui.resumable_uploads import CHUNK_SIZE


def install(app, dashboard, csrf):
    catalog = dashboard.web_catalog

    @app.before_request
    def shared_request():
        if request.path == '/readyz':
            ready = catalog.ready()
            return jsonify(status='ready' if ready else 'resources_initializing'), 200 if ready else 503
        if not request.path.startswith('/api/') or request.path == '/api/session' or request.path.startswith('/api/cos/'):
            return None
        if not catalog.ready():
            return jsonify(error='共享资源正在初始化，请确认 Worker 已启动；失败详情见 Worker 日志'), 503
        # Uploaded chunks and progress do not depend on the local catalog projection.
        response = dispatch(dashboard, csrf)
        if response is not None:
            return response
        catalog.lock.acquire()
        g.catalog_locked = True
        catalog.sync()
        return None

    @app.teardown_request
    def release_catalog(_error):
        if getattr(g, 'catalog_locked', False):
            g.catalog_locked = False
            catalog.lock.release()


def session_token(dashboard):
    return dashboard.web_catalog.state.once('session', {'token': secrets.token_urlsafe(48)})['value']['token']


def dispatch(app, csrf):
    endpoint = request.endpoint or ''
    if endpoint.startswith('zip_tool.'):
        return zip_request(app, csrf, endpoint.split('.')[-1])
    if endpoint in UPLOAD_ROUTES:
        if request.method != 'GET':
            import os
            if os.environ.get('BENCH_CASE_UPLOAD') != '1':
                return jsonify(error='当前部署未启用资源上传'), 404
        return upload_request(app, endpoint)
    if endpoint in {'profiles', 'update_model_profile', 'delete_model_profile'}:
        return model_request(app, endpoint)
    return None


UPLOAD_ROUTES = {'start_upload', 'upload_status', 'current_upload', 'upload_chunk',
                 'finish_upload', 'cancel_upload', 'case_import_status'}


def upload_request(app, endpoint):
    uploads = app.shared_uploads
    args = request.view_args or {}
    identifier = args.get('upload_id')
    actions = {
        'start_upload': lambda: ({'upload': uploads.create(request.get_json(silent=True))}, 201),
        'upload_status': lambda: ({'upload': uploads.view(uploads.read(identifier)['value'])}, 200),
        'current_upload': lambda: ({'upload': uploads.current(request.args.get('kind', ''))}, 200),
        'upload_chunk': lambda: ({'upload': _chunk(uploads, identifier, args['index'] * CHUNK_SIZE)}, 200),
        'finish_upload': lambda: ({'task': uploads.complete(identifier), 'message': '资源包已保存，等待后台处理'}, 202),
        'cancel_upload': lambda: ({'upload': uploads.abort(identifier)}, 200),
        'case_import_status': lambda: ({'task': uploads.import_status(request.args.get('id'))}, 200),
    }
    body, code = actions[endpoint]()
    return jsonify(body), code


def _chunk(uploads, identifier, offset):
    if request.mimetype != 'application/octet-stream':
        raise ValueError('上传分块必须为二进制内容')
    return uploads.chunk(identifier, offset, request.stream, request.content_length)


def model_request(app, endpoint):
    from webui.profiles import create_profile, update_profile, delete_profile
    catalog = app.web_catalog
    if endpoint == 'profiles':
        return jsonify(catalog.change_model(create_profile, request.get_json())), 201
    identifier = request.view_args['profile_id']
    if endpoint == 'update_model_profile':
        return jsonify(catalog.change_model(update_profile, identifier, request.get_json()))
    return jsonify(catalog.change_model(delete_profile, identifier))


def zip_request(app, csrf, action):
    from repository_migration.store import limits
    uploads = app.shared_uploads
    args = request.view_args or {}
    identifier = args.get('identifier')
    actions = {
        'listing': lambda: jsonify(tasks=_zip_rows(uploads), limits=limits()),
        'create': lambda: (jsonify(task=uploads.create(request.get_json(), tool=True)), 201),
        'chunk': lambda: jsonify(task=_chunk(uploads, identifier, args['offset'])),
        'complete': lambda: (jsonify(task=uploads.complete(identifier)), 202),
        'remove': lambda: jsonify(task=uploads.abort(identifier, delete=True), ok=True),
        'ticket': lambda: _ticket(uploads, identifier, csrf),
        'download': lambda: _download(app, identifier),
    }
    return actions[action]()


def _zip_rows(uploads):
    return [uploads.view(row['value']) for row in uploads.rows() if row['value']['kind'] == 'zip']


def _ready(uploads, identifier):
    row = uploads.read(identifier)['value']
    if row['kind'] != 'zip' or row['status'] != 'succeeded' or row['expires_at'] <= time.time():
        raise ValueError('文件尚未就绪或已过期')
    return row


def _ticket(uploads, identifier, csrf):
    from webui.download_tickets import serializer
    _ready(uploads, identifier)
    payload = request.get_json()
    kind = payload.get('kind', 'zip') if isinstance(payload, dict) else 'zip'
    if kind not in {'zip', 'manifest', 'instructions'}:
        raise ValueError('下载类型无效')
    path = f'/api/zip-tool/{identifier}/download'
    token = serializer(csrf).dumps([path, kind])
    return jsonify(url=path + '?' + urlencode({'kind': kind, 'ticket': token}))


def _download(app, identifier):
    row = _ready(app.shared_uploads, identifier)
    names = {'zip': row['output_file'], 'manifest': 'migration_manifest.json', 'instructions': '推送说明.txt'}
    kind = request.args.get('kind', 'zip')
    if kind not in names:
        raise ValueError('下载类型无效')
    name = names[kind]
    temporary = tempfile.TemporaryDirectory(dir=app.data)
    try:
        path = Path(temporary.name) / 'download'
        app.web_catalog.store.get(row['output'][name], path)
        response = send_file(path, as_attachment=True, download_name=name, conditional=True)
        response.direct_passthrough = False
        response.call_on_close(temporary.cleanup)
        return response
    except BaseException:
        temporary.cleanup()
        raise
