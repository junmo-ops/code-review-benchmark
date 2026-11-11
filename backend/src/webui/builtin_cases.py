"""Delete only the explicitly listed bundled case files."""
import hashlib
from pathlib import Path


def delete(dashboard, identifier, action):
    from .service import CATALOG
    from .resource_dependencies import ensure_resource_unused
    allowed = {name for suite in CATALOG for name in suite['datasets']}
    if identifier not in allowed or action != 'delete':
        raise ValueError('内置案例集仅支持删除已列出的案例文件')
    base = Path(dashboard.root) / 'data'
    path = base / identifier
    if path.is_symlink() or path.resolve().parent != base.resolve():
        raise ValueError('内置案例集文件路径无效')
    if not path.is_file():
        raise ValueError('内置案例集已删除，请刷新列表')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    ensure_resource_unused(dashboard, dataset_sha256=digest)
    try:
        path.unlink()
    except OSError as error:
        raise ValueError('无法删除内置案例文件，请检查服务对 data 目录的写权限') from error
    return {'message': '内置案例集已删除；历史批次和结果保留。覆盖完整代码包或重建镜像可能恢复该文件。'}
