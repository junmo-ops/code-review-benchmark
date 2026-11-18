"""Short MySQL transactions; no execution or object uploads inside a transaction."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
from runner.paths import PROJECT_ROOT

from .config import MYSQL_CONNECTION_FIELDS, MYSQL_CREDENTIALS, legacy_database_path, mysql_options


def _environment_settings(root):
    missing = [name for name in MYSQL_CREDENTIALS.values() if not os.environ.get(name, '').strip()]
    if missing:
        raise ValueError('数据库环境变量缺失或为空：' + '、'.join(missing))
    values = mysql_options(root)
    values.update({key: os.environ[name] for key, name in MYSQL_CREDENTIALS.items()})
    return values


def _validate_settings(settings):
    if not isinstance(settings, dict) or set(settings) - (MYSQL_CONNECTION_FIELDS | set(MYSQL_CREDENTIALS)):
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
    def from_config(cls, path=None, *, root=PROJECT_ROOT):
        legacy = Path(path) if path is not None else legacy_database_path(root)
        has_credentials = any(name in os.environ for name in MYSQL_CREDENTIALS.values())
        if legacy is not None and not has_credentials and legacy.exists():
            return cls.from_file(legacy)
        return cls(_validate_settings(_environment_settings(root)))

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
