"""One independent ZIP conversion process per local data directory."""
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

from runner.locking import acquire, release
from . import store
from .archives import prepare_staged, archive_project_name


def start(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'worker.lock').open('a+b') as lock:
        try:
            acquire(lock)
        except BlockingIOError:
            return
        release(lock)
    # No model, benchmark queue, repository registry or COS is involved.
    with (root / 'launcher.log').open('ab') as output:
        subprocess.Popen([sys.executable, '-m', 'repository_migration.worker', str(root)],
                         stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                         start_new_session=os.name != 'nt', close_fds=True)


class Progress:
    def __init__(self, folder, row):
        self.folder, self.row = folder, row

    def __call__(self, message):
        self.row.update(message=message, status='running')
        store.save(self.folder, self.row)


def execute(root, row):
    folder = root / row['id']
    with store.locked(folder / 'lock'):
        progress = Progress(folder, row)
        work = folder / 'work'
        shutil.rmtree(work, ignore_errors=True)
        for leftover in folder.glob('bench-zip-*'):
            shutil.rmtree(leftover, ignore_errors=True)
        try:
            progress('正在校验 ZIP')
            source = folder / 'input.zip'
            name = archive_project_name(source)
            config = {}
            if row['main']:
                from .archives import repository_path
                config[repository_path(source)] = {'main': row['main']}
            result = prepare_staged(source, work, config, folder, name, progress=progress)
            row.update(status='succeeded', message='处理完成，可下载', output_file=name,
                       commits=result['commits'], branches=1 + result['preserved_branches'])
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
            logging.exception('ZIP conversion failed: %s', row['id'])
            row.update(status='failed', message=str(error)[-1500:])
            shutil.rmtree(work, ignore_errors=True)
        finally:
            (folder / 'input.zip').unlink(missing_ok=True)
            row['expires_at'] = time.time() + store.TTL
            store.save(folder, row)


def run(root):
    with (root / 'worker.lock').open('a+b') as lock:
        try:
            acquire(lock)
        except BlockingIOError:
            return
        try:
            store.cleanup(root)
            # A prior process that owned this exclusive lock has stopped. Restart
            # its disposable conversion from the uploaded original, never half-output.
            rows = store.listing(root)
            for row in reversed(rows):
                if row['status'] in {'queued', 'running'}:
                    execute(root, row)
        finally:
            release(lock)


if __name__ == '__main__':
    run(Path(sys.argv[1]).resolve())
