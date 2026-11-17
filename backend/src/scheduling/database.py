"""Short MySQL transactions; no execution or object uploads inside a transaction."""
from contextlib import contextmanager
import json
from pathlib import Path


class Database:
    def __init__(self, settings):
        self.settings = dict(settings)

    @classmethod
    def from_file(cls, path):
        settings = json.loads(Path(path).read_text(encoding='utf-8'))
        allowed = {'host', 'port', 'user', 'password', 'database', 'ssl'}
        if not isinstance(settings, dict) or set(settings) - allowed:
            raise ValueError('数据库配置字段无效')
        if not all(settings.get(key) for key in ('host', 'user', 'password', 'database')):
            raise ValueError('数据库配置缺少 host/user/password/database')
        return cls(settings)

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
