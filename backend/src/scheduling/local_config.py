"""Load the optional local cluster profile before Web or Worker startup."""
import json
import os
from pathlib import Path

from runner.paths import PROJECT_ROOT
from .contracts import identifier

PROFILE_NAME = 'cluster.local.json'
_SETTINGS_ENV = 'BENCH_LOCAL_CLUSTER_SETTINGS'
MYSQL_CREDENTIALS = {'user': 'BENCH_DB_USER', 'password': 'BENCH_DB_PASSWORD'}
COS_CREDENTIALS = {
    'access_key_id': 'BENCH_CASE_S3_ACCESS_KEY_ID',
    'secret_access_key': 'BENCH_CASE_S3_SECRET_ACCESS_KEY',
    'session_token': 'BENCH_CASE_S3_SESSION_TOKEN',
}
CLUSTER_CREDENTIALS = (*MYSQL_CREDENTIALS.values(), *COS_CREDENTIALS.values())
_COS_REQUIRED = ('endpoint', 'region', 'bucket', 'prefix', 'run_prefix', 'access_key_id', 'secret_access_key')


def _local_process():
    return not (os.environ.get('BENCH_SERVER_MODE') or os.environ.get('BENCH_CONTAINER_ROLE'))


def active_local_settings():
    if _local_process() and (encoded := os.environ.get(_SETTINGS_ENV)):
        return json.loads(encoded)
    return None


def _read_profile(root):
    try:
        profile = json.loads((Path(root) / PROFILE_NAME).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise ValueError(f'无法读取 {PROFILE_NAME}，请检查文件权限与 UTF-8 JSON 格式') from None
    if not isinstance(profile, dict) or set(profile) != {'scope', 'mysql', 'cos'}:
        raise ValueError(f'{PROFILE_NAME} 须包含 scope、mysql 和 cos 三项')
    return profile


def _cos_settings(values):
    if not isinstance(values, dict) or set(values) - {*_COS_REQUIRED, 'session_token', 'timeout_ms'}:
        raise ValueError(f'{PROFILE_NAME} 的 cos 配置字段无效')
    for field in _COS_REQUIRED:
        value = values.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{PROFILE_NAME} 的 cos.{field} 须填写非空字符串')
    if not isinstance(values.get('session_token', ''), str):
        raise ValueError(f'{PROFILE_NAME} 的 cos.session_token 须为字符串')
    return dict(values)


def _profile_settings(profile):
    from .database import _validate_settings

    try:
        scope = identifier(profile['scope'], 64)
        mysql = dict(_validate_settings(profile['mysql']))
    except ValueError as error:
        raise ValueError(f'{PROFILE_NAME}：{error}') from None
    return scope, mysql, _cos_settings(profile['cos'])


def load_local_cluster(root=PROJECT_ROOT):
    if not _local_process():
        return
    profile = _read_profile(root)
    if profile is None:
        return
    scope, mysql, cos = _profile_settings(profile)
    credentials = {env: mysql.pop(key) for key, env in MYSQL_CREDENTIALS.items()}
    credentials.update({env: cos.pop(key, '') for key, env in COS_CREDENTIALS.items()})
    # Worker subprocesses inherit the same connection settings and credentials.
    # Only the launchers read the file; review subprocesses lose the credential variables.
    settings = {'scope': scope, 'mysql': mysql, 'cos': cos}
    os.environ.update(credentials)
    os.environ['BENCH_CASE_STORAGE_MODE'] = 'cos'
    os.environ[_SETTINGS_ENV] = json.dumps(settings)
