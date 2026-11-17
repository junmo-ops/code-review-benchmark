"""Prevent unported whole-batch mutations from bypassing the distributed queue."""


def guard(dashboard, request):
    if not getattr(dashboard, 'cluster_mode', False):
        return
    if request.path in {'/api/comparisons', '/api/recovery', '/api/cases/manage', '/api/repositories/manage'}:
        raise ValueError('集群运行期间暂不支持此管理操作；避免清理其他实例正在使用的资源')


def export_root(dashboard, key):
    if getattr(dashboard, 'cluster_mode', False):
        return dashboard.materialize(key)
    return dashboard.run_dir(key)
