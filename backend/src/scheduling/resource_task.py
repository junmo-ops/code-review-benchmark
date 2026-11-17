"""Resource subprocess: expensive work and immutable uploads, never SQL publication."""
import argparse
from pathlib import Path
import time
import zipfile
from application.files import now, read, save
from runner.paths import PROJECT_ROOT
from runner.runtime import data_root
from .web_catalog import WebCatalog, scratch, publish_bundles
from .web_uploads import SharedUploads


class Progress:
    def __init__(self, path):
        self.path = path

    def progress(self, **changes):
        previous = read(self.path) if self.path.exists() else {}
        save(self.path, dict(previous, **changes, updated_at=now()))
        if changes.get('message'):
            print(changes['message'], flush=True)


class ResourceExecution:
    def __init__(self, app):
        self.catalog = WebCatalog(app, web=False)
        self.uploads = SharedUploads(self.catalog)

    def execute(self, payload, folder):
        if payload['key'] == 'bootstrap':
            return {'catalogs': self.catalog.seed()}
        value = payload['upload']
        guard = Progress(folder / 'resource-progress.json')
        archive = folder / 'input.zip'
        self.uploads.assemble(value, archive)
        guard.progress(message='上传内容已恢复，正在处理')
        if value['kind'] == 'zip':
            changes, catalog = self._zip(value, archive, folder, guard), None
        else:
            changes, catalog = self._import(value, archive, folder, guard)
        guard.progress(message='文件已保存，等待 Worker 发布结果')
        return dict(changes=changes, catalog=catalog)

    def _import(self, value, archive, folder, guard):
        from webui.case_assets import install, install_repositories
        from webui.skill_assets import install as install_skill
        from webui.git_import import install as install_git
        installers = {'dataset': install, 'repository': install_repositories,
                      'skill': install_skill, 'repository_git': install_git}
        if value['kind'] != 'repository_git' and not zipfile.is_zipfile(archive):
            raise ValueError('上传内容不是有效 ZIP')
        row = self.catalog.state.get('resources')
        data = folder / 'data'
        self.catalog.store.restore(row['value']['snapshot'], data)
        proxy = scratch(self.catalog.app, data)
        def progress(phase, message, completed=0, total=0, **details):
            guard.progress(phase=phase, message=message, completed=completed, total=total, **details)
        result = installers[value['kind']](proxy, archive, progress=progress, persist=False)
        publish_bundles(proxy)
        record = self.catalog.capture(data)
        changes = dict(status='succeeded', phase='complete', ended_at=now(),
                       message=result.get('message', '资源已导入'))
        if result.get('skill_version'):
            changes['skill_version'] = result['skill_version']
        return changes, dict(revision=row['revision'], value={'snapshot': record})

    def _zip(self, value, archive, folder, guard):
        from repository_migration import store, worker
        root = folder / 'zip-tool'
        target = root / value['id']
        target.mkdir(parents=True)
        archive.replace(target / 'input.zip')
        row = self.uploads.task(value)
        store.save(target, row)
        worker.execute(root, row, observer=lambda current: guard.progress(message=current['message']))
        row = store.read(target)
        changes = dict(status=row['status'], message=row['message'], expires_at=time.time() + store.TTL)
        if row['status'] == 'succeeded':
            names = [row['output_file'], 'migration_manifest.json', '推送说明.txt']
            changes.update(output={name: self.catalog.store.put(target / 'work' / name) for name in names},
                           output_file=row['output_file'], commits=row['commits'], branches=row['branches'])
        return changes


def main():
    from webui.service import Dashboard
    from .settings import services
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request')
    args = parser.parse_args()
    payload = read(Path(args.request))
    app = Dashboard(load_jobs=False)
    app.queue, _, app.cluster = services()
    folder = data_root(PROJECT_ROOT)
    try:
        record = ResourceExecution(app).execute(payload, folder)
    except Exception as error:
        save(folder / 'resource-result.json', {'error': app.redact(f'{type(error).__name__}: {error}')})
        raise SystemExit(1) from None
    save(folder / 'resource-result.json', record)
    if record.get('changes', {}).get('status') == 'failed':
        save(folder / 'resource-result.json', dict(record, error=record['changes']['message']))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
