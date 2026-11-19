"""Import read-only single-instance batches into the current scope's run list."""
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile

from deployment.run_storage import RunDataStorage
from runner.evidence import evidence_files
from runner.runtime import data_root
from webui.service import Dashboard, ROOT
from .artifacts import safe_path
from .web_state import WebState

RUN_PREFIX = 'history/'
STATE_PREFIX = 'history-run-'


def is_history(key):
    return isinstance(key, str) and key.startswith(RUN_PREFIX)


def _backup_digest(storage, name, files):
    payload = json.dumps([storage.bucket, storage.prefix, name, files], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def package_run(folder):
    archive = folder.with_name(folder.name + '-results.tar.gz')
    if not archive.is_file():
        with tempfile.TemporaryDirectory(prefix='export-', dir=folder.parent) as temporary:
            staging = Path(temporary) / archive.name
            with tarfile.open(staging, 'w:gz') as output:
                for path in evidence_files(folder):
                    output.add(path, arcname=folder.name + '/' + path.relative_to(folder).as_posix(),
                               recursive=False)
            staging.replace(archive)


class RunHistory:
    def __init__(self, queue, root=ROOT):
        self.root = root
        self.state = WebState(queue)
        self.cache = data_root(root) / 'history-cache'

    def views(self):
        return [row['value']['view'] for row in self.state.listing(STATE_PREFIX)]

    def _record(self, key):
        row = self.state.get(STATE_PREFIX + key.removeprefix(RUN_PREFIX))
        return row['value'] if row else None

    def detail(self, key):
        record = self._record(key)
        if not record:
            return None
        from .rescore import blocked_reason
        view = record['view']
        return dict(view, compatibility_warning=None, rescore_blocked_reason=blocked_reason(view))

    def preview(self, storage):
        seen = {row['name'].removeprefix(STATE_PREFIX) for row in self.state.listing(STATE_PREFIX)}
        return [(name, name in seen) for name in storage.backup_runs()]

    def _reader(self, directory):
        reader = Dashboard(self.root, load_jobs=False)
        reader.data, reader.state = directory, directory / 'ui-state'
        return reader

    def import_run(self, storage, name):
        if self._record(RUN_PREFIX + name):
            return
        files = storage.backup_files(name)
        digest = _backup_digest(storage, name, files)
        reader = self._reader(self._restore(storage, name, files, digest))
        view = reader.describe(name)
        view.update(key=RUN_PREFIX + name, created=reader.config(name).get('created_at') or '',
                    cluster_status='archived')
        self.state.once(STATE_PREFIX + name, dict(name=name, prefix=storage.prefix, bucket=storage.bucket,
                                                digest=digest, view=view))

    def _restore(self, storage, name, files, digest):
        directory = self.cache / digest
        if directory.is_dir():
            return directory
        self.cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='restore-', dir=self.cache) as temporary:
            staging = Path(temporary) / 'verified'
            root = staging / 'runs' / name
            for relative, record in files.items():
                safe_path(relative)
                storage.restore_file(root, relative, record)
            try:
                staging.rename(directory)
            except OSError:
                # A concurrent import or read may have published these same verified bytes.
                if not directory.is_dir():
                    raise
        return directory

    def _materialized(self, key):
        record = self._record(key)
        if not record:
            raise ValueError('找不到这条历史批次')
        name, digest = record['name'], record['digest']
        directory = self.cache / digest
        if not directory.is_dir():
            storage = RunDataStorage(self.root, prefix=record['prefix'])
            if storage.bucket != record['bucket']:
                raise ValueError('当前 COS 桶与该历史批次的来源不同')
            files = storage.backup_files(name)
            if _backup_digest(storage, name, files) != digest:
                raise ValueError('旧备份清单已经变化，请核对原备份')
            directory = self._restore(storage, name, files, digest)
        return directory, name

    def config(self, key):
        directory, name = self._materialized(key)
        return self._reader(directory).config(name)

    def artifact(self, key, relative):
        directory, name = self._materialized(key)
        return self._reader(directory).artifact(name, relative)

    def run_dir(self, key):
        directory, name = self._materialized(key)
        return directory / 'runs' / name

    def materialize_export(self, key):
        folder = self.run_dir(key)
        package_run(folder)
        return folder


def _publish(history, storage, names):
    from botocore.exceptions import BotoCoreError, ClientError
    failed = 0
    for index, name in enumerate(names, start=1):
        print(f'[{index}/{len(names)}] 正在核对并导入 {name} …', flush=True)
        try:
            history.import_run(storage, name)
        except (BotoCoreError, ClientError, KeyError, OSError, RuntimeError, ValueError) as error:
            failed += 1
            print(f'  此批次未导入（{error}）。', flush=True)
        else:
            print('  已恢复。', flush=True)
    print(f'处理完成，已恢复 {len(names) - failed} 条，失败 {failed} 条。', flush=True)
    return int(bool(failed))


def migrate(*, scope, apply, batch, source_prefix):
    from .database import Database
    from .queue import CaseQueue
    from .settings import settings

    print('正在检查 ST 多实例配置并查找旧备份…', flush=True)
    configured = settings()
    if configured['mode'] != 'mysql' or configured['scope'] != scope:
        current = configured.get('scope') or configured['mode']
        raise ValueError(f'当前 Web 使用 {current}，请在 scope 为 {scope} 的多实例 Web 容器中运行')
    history = RunHistory(CaseQueue(Database.from_config(), scope))
    storage = RunDataStorage(prefix=source_prefix)
    candidates = [(name, saved) for name, saved in history.preview(storage) if batch is None or name == batch]
    pending = [name for name, saved in candidates if not saved]
    for name, saved in candidates:
        print(f'  {"已导入" if saved else "待导入"}  {name}', flush=True)
    print(f'{scope} 共找到 {len(candidates)} 条旧批次，剩余 {len(pending)} 条待导入。', flush=True)
    if not candidates:
        raise ValueError(f'当前 COS 桶的 {storage.manifest_prefix}runs/ 下没有符合条件的旧批次')
    if not apply:
        print('这是预览。确认批次后，在命令末尾加 --apply。', flush=True)
        return 0
    return _publish(history, storage, pending)
