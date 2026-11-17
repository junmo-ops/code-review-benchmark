"""Select a deployment backend once; no silent fallback from MySQL to SQLite."""
from runner.paths import PROJECT_ROOT


def create_dashboard(root=PROJECT_ROOT, local=False):
    from scheduling.settings import enabled
    if enabled(root):
        from scheduling.dashboard import ClusterDashboard
        return ClusterDashboard(root)
    if local:
        from .service import Dashboard
        return Dashboard(root)
    from .queue import DurableDashboard
    return DurableDashboard(root)
