"""Bounded retries for read-only queries of an already submitted Agent task."""
import time


TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
QUERY_RETRIES = 3
RETRY_SECONDS = 60


class TransientQueryError(ValueError):
    """A read-only task query failed transiently; submitting again is unsafe."""


def query(request, kind, task_id, deadline, event):
    for attempt in range(QUERY_RETRIES + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('本地等待时限已到，服务端任务未确认停止；可恢复查询原任务')
        try:
            return request('GET', kind, task_id, remaining=remaining)
        except TransientQueryError as exc:
            event('query_error', task_id=task_id, query=kind, attempt=attempt + 1, error=str(exc))
            if attempt == QUERY_RETRIES:
                raise ValueError(f'{exc}；查询重试已用尽，可恢复查询原任务 {task_id}') from None
            delay = min(RETRY_SECONDS, max(0, deadline - time.monotonic()))
            event('query_retry', task_id=task_id, query=kind, retry=attempt + 1, wait_seconds=delay)
            print(f'Agent 查询暂时失败：{exc}；{delay:g} 秒后重试（{attempt + 1}/{QUERY_RETRIES}），继续查询原任务。', flush=True)
            time.sleep(delay)
