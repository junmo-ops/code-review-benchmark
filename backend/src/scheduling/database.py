"""Short MySQL transactions; no execution or object uploads inside a transaction."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
from runner.paths import PROJECT_ROOT


ENV_CREDENTIALS = {'user': 'BENCH_DB_USER', 'password': 'BENCH_DB_PASSWORD'}
CONNECTION_FIELDS = {'host', 'port', 'database', 'ssl'}


def _environment_settings(root):
    missing = [name for name in ENV_CREDENTIALS.values() if not os.environ.get(name, '').strip()]
    if missing:
        raise ValueError('数据库环境变量缺失或为空：' + '、'.join(missing))
    path = Path(root) / 'deployment/database.json'
    values = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(values, dict) or set(values) - CONNECTION_FIELDS:
        raise ValueError('deployment/database.json 仅允许 host/port/database/ssl；账号密码须通过环境变量配置')
    values.update({key: os.environ[name] for key, name in ENV_CREDENTIALS.items()})
    return values


def _validate_settings(settings):
    if not isinstance(settings, dict) or set(settings) - (CONNECTION_FIELDS | set(ENV_CREDENTIALS)):
        raise ValueError('数据库配置字段无效')
    if any(not isinstance(settings.get(key), str) or not settings[key].strip()
           for key in ('host', 'user', 'password', 'database')):
        raise ValueError('数据库配置缺少有效的 host/user/password/database')
    port = settings.get('port', 3306)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('数据库 port 必须为 1–65535 的整数')
    if 'ssl' in settings and not isinstance(settings['ssl'], dict):
        raise ValueError('数据库 ssl 配置必须为 JSON 对象')
    return settings


class Database:
    def __init__(self, settings):
        self.settings = dict(settings)

    @classmethod
    def from_config(cls, path='database.local.json', *, root=PROJECT_ROOT):
        # Deployment uses tracked connection settings and injected credentials together.
        # Keep local file connections only when no credential variable is present.
        if any(name in os.environ for name in ENV_CREDENTIALS.values()) or not Path(path).exists():
            return cls(_validate_settings(_environment_settings(root)))
        return cls.from_file(path)

    @classmethod
    def from_file(cls, path):
        settings = json.loads(Path(path).read_text(encoding='utf-8'))
        return cls(_validate_settings(settings))

    @contextmanager
    def transaction(self):
        import pymysql
        connection = pymysql.connect(**self.settings, charset='utf8mb4', autocommit=False,
                                     connect_timeout=10, read_timeout=15, write_timeout=15,
                                     cursorclass=pymysql.cursors.DictCursor)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET time_zone = '+00:00'")
                cursor.execute('SET SESSION innodb_lock_wait_timeout = 10')
                yield cursor
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        # Explicit administrator operation. Web/worker startup must never execute DDL.
        script = '\n'.join(Path(__file__).with_name(name).read_text(encoding='utf-8')
                           for name in ('schema.sql', 'web_schema.sql'))
        with self.transaction() as cursor:
            for statement in script.split(';'):
                if statement.strip():
                    cursor.execute(statement)
