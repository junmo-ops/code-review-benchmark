"""Versioned shared catalogs; local files are disposable request-time projections."""
import copy
from pathlib import Path
import shutil
import tempfile
import threading
from webui.service import Dashboard
from .artifacts import Artifacts
from .web_state import WebState

RESOURCE_PATHS = ('case-datasets', 'case-repositories/registry.json', 'case-assets/records', 'skill-assets')


def copy_resources(source, target):
    for name in RESOURCE_PATHS:
        origin, destination = Path(source) / name, Path(target) / name
        if origin.is_symlink():
            raise ValueError('资源目录不能是符号链接')
        if origin.is_dir():
            shutil.copytree(origin, destination, dirs_exist_ok=True, symlinks=True)
        elif origin.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, destination)


def scratch(app, data):
    proxy = Dashboard(app.root, load_jobs=False)
    proxy.data, proxy.state = Path(data), Path(data) / 'ui-state'
    proxy.state.mkdir(parents=True, exist_ok=True)
    proxy.jobs = copy.deepcopy(app.jobs)
    proxy.queue = app.queue
    proxy.shared_catalog = True
    return proxy


def managed_models(configs):
    for path in Path(configs).glob('model*.local.json'):
        if not path.name.startswith('model.run-'):
            yield path
            label = path.with_suffix('.label')
            if label.exists():
                yield label


class VerifiedArtifacts(Artifacts):
    """A resource is published only after bytes are read back and hashed."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.confirmed = set()

    def put(self, path):
        record = super().put(path)
        digest = record['sha256']
        if digest not in self.confirmed:
            with tempfile.TemporaryDirectory() as temporary:
                self.get(record, Path(temporary) / 'verify')
            if len(self.confirmed) >= 4096:
                self.confirmed.clear()
            self.confirmed.add(digest)
        return record


class WebCatalog:
    def __init__(self, app, *, web=True):
        self.app = app
        self.state = WebState(app.queue)
        self.store = VerifiedArtifacts(app.cluster['scope'], max_file=2 * 1024 ** 3)
        self.private = VerifiedArtifacts(app.cluster['scope'])
        from deployment.model_storage import ModelProfileStorage
        legacy = ModelProfileStorage(app.root)
        self.private.prefix = legacy.prefix.rsplit('/v1/', 1)[0] + '/cluster/' + app.cluster['scope'] + '/'
        self.lock = threading.RLock()
        self.versions = {}
        # Fail before listening if the DBA has not applied web_schema.sql.
        self.state.get('resources')
        self.legacy = legacy
        if web:
            from .resources import bootstrap
            bootstrap(app.queue)

    def seed(self):
        catalogs = {}
        if not self.state.get('resources'):
            catalogs['resources'] = self._seed_resources()
        if not self.state.get('models'):
            catalogs['models'] = self._seed_models(self.legacy)
        return catalogs

    def ready(self):
        return bool(self.state.get('resources') and self.state.get('models'))

    def require_ready(self):
        if not self.ready():
            raise ValueError('共享资源尚未初始化，请确认 Worker 已启动并查看其初始化日志')

    def _seed_resources(self):
        from webui.case_storage import CaseAssetStorage, CaseArchiveStorage
        from webui.skill_storage import SkillAssetStorage
        with tempfile.TemporaryDirectory(dir=self.app.data) as temporary:
            proxy = scratch(self.app, temporary)
            copy_resources(self.app.data, proxy.data)
            CaseAssetStorage(proxy.data).restore(proxy)
            CaseArchiveStorage(proxy.data).restore(proxy)
            SkillAssetStorage(proxy.data).restore(proxy)
            record = self.capture(proxy.data)
        return record

    def _seed_models(self, legacy):
        with tempfile.TemporaryDirectory(dir=self.app.data) as temporary:
            folder = Path(temporary)
            for path in managed_models(self.app.configs):
                if path.is_symlink():
                    raise ValueError('模型配置不能是符号链接')
                shutil.copyfile(path, folder / path.name)
            legacy.configs = folder
            for key in legacy.list_profiles():
                # Legacy restore verifies credentials/fields without exposing them to SQL.
                legacy.restore(key)
            for path in folder.glob('model.run-*'):
                path.unlink()
            record = self.private.capture(folder)
        return record

    def capture(self, data):
        with tempfile.TemporaryDirectory(dir=self.app.data) as temporary:
            copy_resources(data, temporary)
            return self.store.capture(temporary)

    def sync(self):
        self.require_ready()
        with self.lock:
            self._sync_resources()
            self._sync_models()

    def _sync_resources(self):
        row = self.state.get('resources')
        if self.versions.get('resources') == row['revision']:
            return
        with tempfile.TemporaryDirectory(dir=self.app.data) as temporary:
            self.store.restore(row['value']['snapshot'], temporary)
            for name in RESOURCE_PATHS:
                old = self.app.data / name
                if old.is_dir():
                    shutil.rmtree(old)
                else:
                    old.unlink(missing_ok=True)
            copy_resources(temporary, self.app.data)
        self.versions['resources'] = row['revision']

    def _sync_models(self):
        row = self.state.get('models')
        if self.versions.get('models') == row['revision']:
            return
        with tempfile.TemporaryDirectory(dir=self.app.data) as temporary:
            self.private.restore(row['value']['snapshot'], temporary)
            for path in managed_models(self.app.configs):
                path.unlink()
            self.app.configs.mkdir(parents=True, exist_ok=True)
            from webui.profiles import _write_private
            for path in managed_models(temporary):
                _write_private(self.app.configs / path.name, path.read_bytes())
        self.versions['models'] = row['revision']

    def change_model(self, operation, *args):
        self.require_ready()
        with self.lock:
            row = self.state.get('models')
            with tempfile.TemporaryDirectory(dir=self.app.data) as temporary:
                folder = Path(temporary)
                self.private.restore(row['value']['snapshot'], folder)
                proxy = scratch(self.app, folder / 'data')
                proxy.configs = folder
                result = operation(proxy, *args, persist=False)
                # Only model files are published, never scratch data or diagnostics.
                shutil.rmtree(folder / 'data')
                record = self.private.capture(folder)
                self.state.put('models', {'snapshot': record}, row['revision'])
            self._sync_models()
            return result


def publish_bundles(proxy):
    """Upload only immutable bundles. The SQL catalog publishes their registry."""
    from webui.case_assets import repository_registry, _registry
    from webui.case_storage import CaseAssetStorage
    if not any((proxy.data / 'case-repositories/objects').glob('*.bundle')):
        return
    storage = CaseAssetStorage(proxy.data)
    registry = repository_registry(proxy.data)
    for slug, entry in registry['repositories'].items():
        for record in entry['versions']:
            path = proxy.data / 'case-repositories/objects' / (record['sha256'] + '.bundle')
            if path.is_file():
                with path.open('rb') as stream:
                    storage._put_stream(storage._repository_pool_key(slug, record['sha256']), stream,
                                        record['size'], record['sha256'], 'application/octet-stream')
    # Bundled case packages also use the immutable repository pool after import.
    rows = _registry(proxy.data)
    for row in rows['datasets']:
        for slug, record in row.get('repository_versions', {}).items():
            path = proxy.data / 'case-repositories/objects' / (record.get('sha256', '') + '.bundle')
            if path.is_file() and record.get('source') == 'case-archive':
                record['source'] = 'repository-pool'
    from application.files import save
    save(proxy.data / 'case-datasets/registry.json', rows)
