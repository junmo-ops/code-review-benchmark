"""Persistent task identities and lease capabilities."""
from dataclasses import dataclass
import hashlib
import json
import re


class LeaseLost(RuntimeError):
    """The owner may no longer publish or continue model calls for this attempt."""


def identifier(value, maximum=100):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value) or len(value) > maximum:
        raise ValueError('调度标识只能包含字母、数字、下划线和连字符')
    return value


def positive(value):
    if type(value) is not int or not 1 <= value <= 10000:
        raise ValueError('调度限额必须是 1–10000 的整数')
    return value


def encode(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(text.encode('utf-8')) > 1024 * 1024:
        raise ValueError('调度记录超过 1 MiB；大文件应保存到对象存储')
    return text


def decode(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def task_identity(run_id, case_id):
    identifier(run_id)
    if not isinstance(case_id, str) or not case_id or len(case_id) > 512:
        raise ValueError('案例标识无效')
    return hashlib.sha256(encode([run_id, case_id]).encode()).hexdigest()


@dataclass(frozen=True)
class Lease:
    scope: str
    task_id: str
    owner: str
    token: str
    generation: int
    payload: dict
    checkpoint: dict | None
