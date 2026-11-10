"""HTTP boundary for standalone repository ZIP conversion."""
import time

from flask import Blueprint, jsonify, request, send_file
from repository_migration import store, worker


def register(app, dashboard, csrf):
    routes = Blueprint('zip_tool', __name__)
    root = dashboard.data / 'zip-tool'

    @routes.get('/api/zip-tool')
    def listing():
        store.cleanup(root)
        rows = store.listing(root)
        if any(row['status'] in {'queued', 'running'} for row in rows):
            worker.start(root)
        return jsonify(tasks=rows, limits=store.limits())

    @routes.post('/api/zip-tool')
    def create():
        return jsonify(task=store.create(root, request.get_json())), 201

    @routes.post('/api/zip-tool/<identifier>/chunks/<int:offset>')
    def chunk(identifier, offset):
        if request.mimetype != 'application/octet-stream':
            raise ValueError('分块必须为二进制内容')
        return jsonify(task=store.put_chunk(root, identifier, offset, request.stream, request.content_length))

    @routes.post('/api/zip-tool/<identifier>/complete')
    def complete(identifier):
        row = store.complete(root, identifier)
        worker.start(root)
        return jsonify(task=row), 202

    @routes.delete('/api/zip-tool/<identifier>')
    def remove(identifier):
        store.remove(root, identifier)
        return jsonify(ok=True)

    @routes.post('/api/zip-tool/<identifier>/download-ticket')
    def ticket(identifier):
        from urllib.parse import urlencode
        from .download_tickets import serializer
        folder = store.folder_for(root, identifier)
        if store.read(folder)['status'] != 'succeeded':
            raise ValueError('文件尚未就绪')
        payload = request.get_json()
        kind = payload.get('kind', 'zip') if isinstance(payload, dict) else 'zip'
        if kind not in {'zip', 'manifest', 'instructions'}:
            raise ValueError('下载类型无效')
        path = f'/api/zip-tool/{identifier}/download'
        token = serializer(csrf).dumps([path, kind])
        return jsonify(url=path + '?' + urlencode({'kind': kind, 'ticket': token}))

    @routes.get('/api/zip-tool/<identifier>/download')
    def download(identifier):
        folder = store.folder_for(root, identifier)
        row = store.read(folder)
        if row['status'] != 'succeeded' or row['expires_at'] <= time.time():
            raise ValueError('文件尚未就绪或已过期')
        kind = request.args.get('kind', 'zip')
        names = {'zip': row['output_file'], 'manifest': 'migration_manifest.json', 'instructions': '推送说明.txt'}
        if kind not in names:
            raise ValueError('下载类型无效')
        path = folder / 'work' / names[kind]
        return send_file(path, as_attachment=True, download_name=names[kind], conditional=True)

    app.register_blueprint(routes)
