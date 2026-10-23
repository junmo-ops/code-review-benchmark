"""Test-only COS connection and restart gates; only filesystem objects are used."""
import importlib.util
import json
import os
from pathlib import Path
import threading
from unittest.mock import patch


def configure(stack, data, root):
    spec = importlib.util.spec_from_file_location('local_cos', root / 'backend/tests/local_cos.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    remote = module.LocalCOS(data.parent / 'local-cos')
    os.environ.update(BENCH_CASE_STORAGE_MODE='cos', BENCH_RUN_STORAGE_MODE='cos',
                      BENCH_CASE_S3_ACCESS_KEY_ID='local', BENCH_CASE_S3_SECRET_ACCESS_KEY='local-only')
    stack.enter_context(patch('webui.case_storage.CaseArchiveStorage._create_client', return_value=remote))
    from deployment.run_storage import RunDataStorage, RunDataSynchronizer, restore_run_data
    from webui.case_storage import restore_case_archives
    restore_run_data(root)
    restore_case_archives(root)
    synchronizer = RunDataSynchronizer(RunDataStorage(root), interval=3600)
    # Explicit test gates avoid a background sync racing the replacement/fault window.
    stopping = threading.Event()
    stack.callback(synchronizer.stop)
    stack.callback(stopping.set)
    thread = threading.Thread(target=commands, args=(data, synchronizer, stopping), daemon=True)
    thread.start()
    return remote


def commands(data, synchronizer, stopping):
    command = data / 'storage-command.json'
    response = data / 'storage-response.json'
    while not stopping.wait(.05):
        if not command.exists():
            continue
        value = json.loads(command.read_text())
        command.unlink()
        synchronizer.sync_once(force=True)
        response.write_text(json.dumps(value))


def enable(stack, data, root):
    if os.environ.get('E2E_SKILL_COS') != '1':
        return
    configure(stack, data, root)
