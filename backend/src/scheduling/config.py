"""Select one cluster profile for Web, Worker, and scheduling commands."""
import json
import os
from pathlib import Path

from runner.paths import PROJECT_ROOT
from .contracts import identifier

LOCAL_PROFILE = 'cluster.local.json'
DEPLOYMENT_PROFILE = 'deployment/cluster.json'
_LOCAL_SETTINGS_ENV = 'BENCH_LOCAL_CLUSTER_SETTINGS'
MYSQL_CONNECTION_FIELDS = {'host', 'port', 'database', 'ssl'}
MYSQL_CREDENTIALS = {'user': 'BENCH_DB_USER', 'password': 'BENCH_DB_PASSWORD'}
COS_CREDENTIALS = {
    'access_key_id': 'BENCH_CASE_S3_ACCESS_KEY_ID',
    'secret_access_key': 'BENCH_CASE_S3_SECRET_ACCESS_KEY',
    'session_token': 'BENCH_CASE_S3_SESSION_TOKEN',
}
CLUSTER_CREDENTIALS = (*MYSQL_CREDENTIALS.values(), *COS_CREDENTIALS.values())
_COS_CONNECTION_FIELDS = {'endpoint', 'region', 'bucket', 'prefix', 'run_prefix'}
_COS_FIELDS = _COS_CONNECTION_FIELDS | {'mode', 'timeout_ms'}
_SECTIONS = {'mysql', 'cos'}


def _local_process():
    return not (os.environ.get('BENCH_SERVER_MODE') or os.environ.get('BENCH_CONTAINER_ROLE'))


def _read_object(path, *, optional=False):
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        if optional:
            return None
        raise ValueError(f'找不到 {path.name}') from None
    except (OSError, ValueError):
        raise ValueError(f'无法读取 {path.name}，请检查文件权限与 UTF-8 JSON 格式') from None
    if not isinstance(value, dict):
        raise ValueError(f'{path.name} 须为 JSON 对象')
    return value


def _profile(root):
    if _local_process() and (encoded := os.environ.get(_LOCAL_SETTINGS_ENV)):
        return json.loads(encoded), True
    value = _read_object(Path(root) / DEPLOYMENT_PROFILE, optional=True)
    return (value if value is not None else {'mode': 'single'}), False


def cluster_options(root=PROJECT_ROOT):
    profile, local = _profile(root)
    options = {key: value for key, value in profile.items() if key not in _SECTIONS | {'concurrency'}}
    scope = os.environ.get('BENCH_CLUSTER_SCOPE')
    if options.get('mode') == 'mysql' and not local and scope is not None:
        try:
            options['scope'] = identifier(scope.strip(), 64)
        except ValueError as error:
            raise ValueError('BENCH_CLUSTER_SCOPE 须为 1–64 位字母、数字、下划线或连字符') from error
    return options


def legacy_database_path(root=PROJECT_ROOT):
    profile, _ = _profile(root)
    if _SECTIONS.intersection(profile):
        return None
    return Path(root) / profile.get('database_config', 'database.local.json')


def mysql_options(root=PROJECT_ROOT):
    profile, _ = _profile(root)
    if _SECTIONS.intersection(profile):
        options = profile.get('mysql')
        source = DEPLOYMENT_PROFILE + ' 的 mysql'
    else:
        source = 'deployment/database.json'
        options = _read_object(Path(root) / source)
    if not isinstance(options, dict) or options.keys() - MYSQL_CONNECTION_FIELDS:
        raise ValueError(f'{source} 仅允许 host、port、database 和 ssl，账号密码须由凭据配置提供')
    return dict(options)


def _cos_fields(profile, root):
    if _SECTIONS.intersection(profile):
        value = profile.get('cos')
        if not isinstance(value, dict) or value.keys() - _COS_FIELDS:
            raise ValueError(f'{DEPLOYMENT_PROFILE} 的 cos 配置字段无效，凭据须由环境变量提供')
        options = dict(value)
        return options.pop('mode', 'cos'), options
    legacy = _read_object(Path(root) / 'deployment/case-storage.json')
    return legacy.get('mode'), legacy.get('s3')


def _prefix_overrides(options):
    result = dict(options)
    for variable, key in (('BENCH_COS_PREFIX', 'prefix'), ('BENCH_COS_RUN_PREFIX', 'run_prefix')):
        if variable not in os.environ:
            continue
        value = os.environ[variable].strip().strip('/')
        if not value or any(part in {'', '.', '..'} for part in value.split('/')):
            raise RuntimeError(variable + ' 须为非空 COS 前缀，不能包含空路径段、. 或 ..')
        result[key] = value
    return result


def cos_options(root=PROJECT_ROOT):
    profile, local = _profile(root)
    storage_mode, options = _cos_fields(profile, root)
    if storage_mode not in ('local', 'cos') or not isinstance(options, dict):
        raise ValueError('COS 配置须指定 local 或 cos 存储模式及连接参数')
    mode = os.environ.get('BENCH_CASE_STORAGE_MODE', storage_mode).strip().lower()
    if mode not in ('local', 'cos'):
        raise ValueError('BENCH_CASE_STORAGE_MODE 须为 local 或 cos')
    return mode, options if local else _prefix_overrides(options)


def _local_cos(values):
    if not isinstance(values, dict) or values.keys() - (_COS_FIELDS | COS_CREDENTIALS.keys()):
        raise ValueError(f'{LOCAL_PROFILE} 的 cos 配置字段无效')
    if values.get('mode', 'cos') != 'cos':
        raise ValueError(f'{LOCAL_PROFILE} 的 cos.mode 须为 cos')
    for field in sorted(_COS_CONNECTION_FIELDS | {'access_key_id', 'secret_access_key'}):
        value = values.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{LOCAL_PROFILE} 的 cos.{field} 须填写非空字符串')
    if not isinstance(values.get('session_token', ''), str):
        raise ValueError(f'{LOCAL_PROFILE} 的 cos.session_token 须为字符串')
    return dict(values)


def _local_profile(profile):
    from .database import _validate_settings

    required = {'scope', *_SECTIONS}
    # Accept the retired field in existing local profiles without applying it.
    if required - profile.keys() or profile.keys() - (required | {'mode', 'concurrency'}):
        raise ValueError(f'{LOCAL_PROFILE} 须包含 scope、mysql 和 cos')
    if profile.get('mode', 'mysql') != 'mysql':
        raise ValueError(f'{LOCAL_PROFILE} 的 mode 须为 mysql')
    try:
        scope = identifier(profile['scope'], 64)
        mysql = dict(_validate_settings(profile['mysql']))
    except ValueError as error:
        raise ValueError(f'{LOCAL_PROFILE}：{error}') from None
    return {'mode': 'mysql', 'scope': scope, 'mysql': mysql, 'cos': _local_cos(profile['cos'])}


def load_local_cluster(root=PROJECT_ROOT):
    if not _local_process():
        return
    value = _read_object(Path(root) / LOCAL_PROFILE, optional=True)
    if value is None:
        return
    profile = _local_profile(value)
    credentials = {env: profile['mysql'].pop(key) for key, env in MYSQL_CREDENTIALS.items()}
    credentials.update({env: profile['cos'].pop(key, '') for key, env in COS_CREDENTIALS.items()})
    # Worker subprocesses inherit resolved settings. Review processes strip cluster credentials.
    os.environ.update(credentials)
    os.environ['BENCH_CASE_STORAGE_MODE'] = 'cos'
    os.environ[_LOCAL_SETTINGS_ENV] = json.dumps(profile)
