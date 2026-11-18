"""Explicit maintenance and confirmed cleanup endpoints."""
from flask import g, jsonify, request


def register(app, dashboard):
    def queue():
        if not getattr(dashboard, 'cluster_mode', False):
            raise ValueError('当前仅支持共享数据库集群的当前 scope 清理；单机数据保持只读')
        if g.bench_role != 'operator':
            raise ValueError('仅操作员可执行 COS 清理')
        return dashboard.queue

    @app.get('/api/cos/maintenance')
    def cleanup_status():
        from scheduling.cos_maintenance import status
        return jsonify(status(queue()))

    @app.post('/api/cos/maintenance')
    def cleanup_maintenance():
        from scheduling.cos_maintenance import change
        data = request.get_json()
        if not isinstance(data, dict) or type(data.get('active')) is not bool:
            raise ValueError('维护状态参数无效')
        return jsonify(change(queue(), data['active']))

    @app.post('/api/cos/cleanup/scan')
    def cleanup_scan():
        from scheduling.cos_maintenance import operation
        from scheduling.cos_cleanup import save_preview
        from scheduling.artifacts import Artifacts
        current = queue()
        with operation(current):
            return jsonify(save_preview(current, Artifacts(current.scope)))

    @app.post('/api/cos/cleanup/delete')
    def cleanup_delete():
        from scheduling.cos_maintenance import operation
        from scheduling.cos_cleanup import delete_preview
        from scheduling.artifacts import Artifacts
        data = request.get_json()
        if not isinstance(data, dict) or data.get('confirmed') is not True or not isinstance(data.get('id'), str):
            raise ValueError('请确认本次预览后再删除')
        current = queue()
        with operation(current):
            return jsonify(delete_preview(current, Artifacts(current.scope), data['id']))
