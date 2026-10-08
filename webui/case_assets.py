"""Install the fixed AACR case ZIP into writable runtime storage, without model calls."""
import fcntl
import hashlib
import json
from pathlib import Path
import tempfile


def status(root, data):
    manifest_path = Path(root) / 'deployment/aacr-cases-manifest.json'
    try:
        marker = json.loads((Path(data) / 'case-assets.json').read_text(encoding='utf-8'))
        ready = marker['manifest_sha256'] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        ready = ready and all((Path(data) / 'repos' / slug / '.git').is_dir()
                              for slug in marker['repositories']) and bool(marker['repositories'])
    except (OSError, ValueError, KeyError, TypeError):
        ready = False
    return dict(enabled=True, ready=bool(ready), name='AACR 五例',
                message='案例已准备，可以新建测评。' if ready else '请先上传 aacr-pilot5-cases.zip，上传和准备案例不会调用模型。')


def install(dashboard, archive):
    from runner.cloud import checked_instances
    from tools.prepare_case_archive import stage
    from tools.setup import restore_repositories
    from .service import save
    root, data = dashboard.root, dashboard.data
    with (dashboard.state / 'case-upload.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('已有案例上传正在处理，请稍后刷新') from None
        if status(root, data)['ready']:
            raise ValueError('案例已准备，无需重复上传')
        if any(job['status'] in {'queued', 'running', 'cancelling'} for job in dashboard.queue.jobs().values()):
            raise ValueError('请先等待测评任务结束，再准备案例')
        manifest_path = root / 'deployment/aacr-cases-manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        cases = checked_instances(root / 'data/pilot5.jsonl', 'aacr')
        repositories = sorted({case.repo.replace('/', '__') for case in cases})
        target = data / 'repos'
        if target.is_symlink():
            raise ValueError('案例仓库目录不能为符号链接')
        target.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='case-prepare-', dir=data) as folder:
            staging = Path(folder)
            stage(archive, manifest, destination_root=staging)
            restore_repositories(cases, bundle_root=staging / 'bundles', repos_root=staging / 'repos')
            # Existing repositories are validated, never overwritten by an upload.
            for slug in repositories:
                destination = target / slug
                if destination.is_symlink():
                    raise ValueError('已有案例仓库不能为符号链接')
                if destination.exists():
                    existing = [case for case in cases if case.repo.replace('/', '__') == slug]
                    restore_repositories(existing, bundle_root=staging / 'bundles', repos_root=target)
            for slug in repositories:
                if not (target / slug).exists():
                    (staging / 'repos' / slug).replace(target / slug)
        save(data / 'case-assets.json', dict(
            manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            repositories=repositories))
        return status(root, data)
