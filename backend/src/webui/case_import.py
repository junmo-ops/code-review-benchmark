"""Background case imports with a disk-backed status and one worker per data root."""
import json
from contextlib import contextmanager
import logging
import re
from pathlib import Path
import threading
import time
import traceback
from types import SimpleNamespace
import uuid

from runner.locking import acquire, release
from .service import now, save

ACTIVE = {'queued', 'running'}
_workers = {}
_guard = threading.Lock()


def _directory(dashboard):
    return dashboard.state / 'case-imports'


def current(dashboard, identifier=None):
    if identifier is not None and not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise ValueError('导入任务标识无效')
    if identifier:
        latest = current(dashboard)
        if latest and latest.get('id') == identifier:
            return latest  # Also supports the current record from older versions.
    path = _directory(dashboard) / ((identifier or 'current') + '.json')
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def ensure_idle(dashboard):
    task = current(dashboard)
    if task and task['status'] in ACTIVE:
        raise ValueError('资源正在后台导入，请等待完成后再操作')


@contextmanager
def benchmark_submission(dashboard):
    """Serialize job admission with resource mutation across API processes."""
    with (dashboard.state / 'case-upload.lock').open('a+b') as lock:
        try:
            acquire(lock)
        except BlockingIOError:
            raise ValueError('资源正在更新，请稍后提交测评任务') from None
        try:
            ensure_idle(dashboard)
            yield
        finally:
            release(lock)


def dataset_pending(data, digest):
    task = current(SimpleNamespace(state=Path(data) / 'ui-state'))
    return bool(task and task.get('dataset_sha256') == digest
                and task['status'] != 'succeeded' and not task.get('previously_registered'))


def _update(dashboard, **changes):
    task = current(dashboard)
    task.update(changes, updated_at=now())
    _save_task(dashboard, task)
    return task


def _save_task(dashboard, task):
    # Keep completed imports addressable after another resource import starts.
    save(_directory(dashboard) / (task['id'] + '.json'), task)
    save(_directory(dashboard) / 'current.json', task)


def submit(dashboard, archive, kind="dataset", upload_id=None):
    folder = _directory(dashboard)
    folder.mkdir(parents=True, exist_ok=True)
    with (dashboard.state / 'case-upload.lock').open('a+b') as lock:
        try:
            acquire(lock)
        except BlockingIOError:
            raise ValueError('正在提交案例导入，请稍后重试') from None
        try:
            with dashboard.lock:
                task = _queue_archive(dashboard, archive, folder, kind, upload_id)
        finally:
            release(lock)
    start(dashboard)
    return dict(task=task, message='资源包已接收，正在后台导入。')


def _queue_archive(dashboard, archive, folder, kind, upload_id=None):
    ensure_idle(dashboard)
    identifier = uuid.uuid4().hex
    target = folder / (identifier + '.zip')
    Path(archive).replace(target)
    task = dict(id=identifier, kind=kind, status='queued', phase='queued', message='已接收，等待后台校验',
                completed=0, total=0, created_at=now(), updated_at=now())
    if upload_id:
        task['upload_id'] = upload_id
    _save_task(dashboard, task)
    return task


def start(dashboard):
    key = str(dashboard.state.resolve())
    with _guard:
        thread = _workers.get(key)
        if thread and thread.is_alive():
            return
        thread = threading.Thread(target=_worker, args=(dashboard,), daemon=True,
                                  name='case-import-worker')
        _workers[key] = thread
        thread.start()


def _worker(dashboard):
    folder = _directory(dashboard)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'worker.lock').open('a+b') as lock:
        try:
            acquire(lock)
        except BlockingIOError:
            return
        try:
            _recover(dashboard)
            while True:
                task = current(dashboard)
                if task and task['status'] == 'queued':
                    _execute(dashboard, task)
                time.sleep(1)
        except Exception:
            logging.getLogger(__name__).error('案例导入后台进程异常：\n%s',
                                              dashboard.redact(traceback.format_exc()))
        finally:
            release(lock)


def _recover(dashboard):
    task = current(dashboard)
    if task and task['status'] == 'running':
        _update(dashboard, status='interrupted', ended_at=now(),
                message='服务曾中断，请重新上传本次导入包；已准备的仓库会复用')
        (_directory(dashboard) / (task['id'] + '.zip')).unlink(missing_ok=True)


def _import_dashboard(dashboard):
    # Keep the web snapshot responsive while install holds its own lock.
    values = dict(root=dashboard.root, data=dashboard.data, state=dashboard.state,
                  jobs=dashboard.jobs, lock=threading.RLock())
    if hasattr(dashboard, 'queue'):
        values['queue'] = dashboard.queue
    return SimpleNamespace(**values)


def _execute(dashboard, task):
    from .case_assets import install, install_repositories
    archive = _directory(dashboard) / (task['id'] + '.zip')
    _update(dashboard, status='running', started_at=now(), message='正在校验资源包')
    def progress(phase, message, completed=0, total=0, **details):
        _update(dashboard, phase=phase, message=message, completed=completed, total=total, **details)
    try:
        proxy = _import_dashboard(dashboard)
        if task.get("kind") == "repository":
            result = install_repositories(proxy, archive, background=True, progress=progress)
        else:
            result = install(proxy, archive, progress=progress)
        _update(dashboard, status='succeeded', phase='complete', ended_at=now(),
                message=result.get('message', '案例集已导入，可新建测评'))
    except Exception as error:
        details = dashboard.redact(traceback.format_exc())
        logging.getLogger(__name__).error('资源导入失败 [%s]：\n%s', task['id'], details)
        _update(dashboard, status='failed', ended_at=now(),
                message=dashboard.redact(f'{type(error).__name__}: {error}'))
    finally:
        archive.unlink(missing_ok=True)
