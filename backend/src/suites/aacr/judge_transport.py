"""Retry judge requests and replay saved successes without paying for them again."""
import asyncio
import hashlib
import json
import os
from types import SimpleNamespace

from runner.storage import save

REQUEST_TIMEOUT = 180
RETRY_DELAYS = (10, 30)
REQUEST_BUDGET = REQUEST_TIMEOUT * (len(RETRY_DELAYS) + 1) + sum(RETRY_DELAYS)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def successful(record):
    answer = record.get('answer')
    return record.get('status') == 'success' and isinstance(answer, str) and bool(answer.strip())


class IncompleteResponse(ValueError):
    pass  # A truncated or empty response cannot become a successful judgment.


def retryable(error):
    from openai import APIConnectionError, APITimeoutError
    status = getattr(error, 'status_code', None)
    return (isinstance(error, (APIConnectionError, APITimeoutError, IncompleteResponse))
            or status in {408, 409, 429, 500, 502, 503, 504})


def response_answer(response):
    if not response.choices:
        raise IncompleteResponse('裁判未返回选择项')
    choice = response.choices[0]
    if getattr(choice, 'finish_reason', 'stop') != 'stop':
        raise IncompleteResponse('裁判回答被截断或未正常结束')
    answer = choice.message.content
    if not isinstance(answer, str) or not answer.strip():
        raise IncompleteResponse('裁判返回空回答')
    return answer


class JudgeTransport:
    def __init__(self, run):
        self.run = run
        path = run / 'judge-requests.json'
        self.records = json.loads(path.read_text()) if path.exists() else []
        if not isinstance(self.records, list) or not all(isinstance(r, dict) for r in self.records):
            raise ValueError('已有裁判请求记录损坏，拒绝覆盖')
        path = run / 'judge-checkpoints.json'
        self.checkpoints = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(self.checkpoints, dict):
            raise ValueError('裁判检查点格式无效')
        self.legacy = [i for i, record in enumerate(self.records) if 'round' not in record]
        self.cursor = 0
        self.round = 0
        self.position = 0
        self.strict_replay = False
        self.last_record = None
        self.last_error = None
        # Recover a success saved immediately before a crash writing its index.
        for i, record in enumerate(self.records):
            if successful(record) and 'round' in record:
                key = f"{record['round']}:{record['request_no']}"
                self.checkpoints.setdefault(key, dict(index=i, request_sha256=record['request_sha256']))

    def begin_round(self, index, completed):
        self.round, self.position = index, 0
        self.strict_replay = completed

    def _remember(self, key, index, request_hash):
        self.checkpoints[key] = dict(index=index, request_sha256=request_hash)
        save(self.run / 'judge-checkpoints.json', self.checkpoints)

    def _cached(self, key, kwargs):
        request_hash = fingerprint(kwargs)
        checkpoint = self.checkpoints.get(key)
        if checkpoint is not None:
            if not isinstance(checkpoint, dict) or checkpoint.get('request_sha256') != request_hash:
                raise ValueError('裁判请求与已有检查点不一致，拒绝复用或覆盖')
            index = checkpoint['index']
            if type(index) is not int or not 0 <= index < len(self.records):
                raise ValueError('裁判检查点指向无效请求记录')
            record = self.records[index]
            if record.get('prompt_sha256') != fingerprint(kwargs['messages']):
                raise ValueError('裁判检查点引用了不同的请求')
            if index in self.legacy:
                self.cursor = max(self.cursor, self.legacy.index(index) + 1)
            return record
        return self._legacy_cached(key, kwargs, request_hash)

    def _legacy_cached(self, key, kwargs, request_hash):
        message_hash = fingerprint(kwargs['messages'])
        while self.cursor < len(self.legacy):
            index = self.legacy[self.cursor]
            record = self.records[index]
            if record.get('prompt_sha256') != message_hash:
                raise ValueError('旧裁判请求与当前答卷不一致，拒绝自动重新评分')
            self.cursor += 1
            if successful(record):
                self._remember(key, index, request_hash)
                return record
        return None

    def _replay(self, record):
        if not successful(record):
            raise ValueError('已有裁判成功记录不完整，拒绝复用')
        self.last_record = record
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=record['answer']))])

    async def create(self, **kwargs):
        self.position += 1
        self.last_record = None
        self.last_error = None
        try:
            return await self._create_or_replay(kwargs)
        except BaseException as error:
            self.last_error = type(error).__name__
            raise

    async def _create_or_replay(self, kwargs):
        key = f'{self.round}:{self.position}'
        record = self._cached(key, kwargs)
        if record is not None:
            return self._replay(record)
        if self.strict_replay:
            raise ValueError('已完成轮次缺少成功请求证据，拒绝重新调用模型')
        return await self._request(key, kwargs)

    async def _request(self, key, kwargs):
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                response = await self._one_request(kwargs, attempt)
                self._remember(key, len(self.records) - 1, fingerprint(kwargs))
                return response
            except Exception as error:
                if not retryable(error) or attempt == len(RETRY_DELAYS):
                    status = getattr(error, 'status_code', None)
                    raise RuntimeError(f'裁判请求失败（HTTP {status or "未返回状态码"}，{type(error).__name__}）；已保存进度，可再次评分续评') from None
                delay = RETRY_DELAYS[attempt]
                print(f'裁判请求失败，{delay} 秒后重试（{attempt + 1}/{len(RETRY_DELAYS)}）；第 {self.round} 轮请求 {self.position}', flush=True)
                await asyncio.sleep(delay)

    async def _one_request(self, kwargs, attempt):
        from openai import AsyncOpenAI
        record = dict(prompt_sha256=fingerprint(kwargs['messages']), request_sha256=fingerprint(kwargs),
                      round=self.round, request_no=self.position, attempt=attempt + 1)
        self.records.append(record)
        self.last_record = record
        # Record in-flight requests too; a killed process must not look successful.
        record['status'] = 'running'
        save(self.run / 'judge-requests.json', self.records)
        try:
            async with AsyncOpenAI(api_key=os.environ['JUDGE_API_KEY'], base_url=os.environ['JUDGE_BASE_URL'],
                                   timeout=REQUEST_TIMEOUT, max_retries=0) as client:
                response = await client.chat.completions.create(**kwargs)
            usage = response.usage.model_dump() if response.usage else None
            record['usage'] = usage
            record.update(answer=response_answer(response), status='success')
            return response
        except Exception as error:
            record.update(status='failed', error_type=type(error).__name__, http_status=getattr(error, 'status_code', None))
            raise
        finally:
            save(self.run / 'judge-requests.json', self.records)
