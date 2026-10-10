"""Write-only model configuration creation; never return credentials."""
import json
import os
import uuid
from urllib.parse import urlsplit

STREAM_ON = {'true', '1', 'yes', 'on'}
STREAM_OFF = {'false', '0', 'no', 'off', ''}


def normalize_stream_mode(value):
    if not isinstance(value, str):
        raise ValueError('流式响应设置无效')
    mode = value.strip().lower()
    if mode in STREAM_ON:
        return 'true'
    if mode in STREAM_OFF:
        return 'false'
    raise ValueError('流式响应设置无效')


def resolve_profile(dashboard, data):
    if not isinstance(data, dict):
        raise ValueError('配置必须是对象')
    def field(name):
        value = data.get(name, '')
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError('配置字段类型或长度无效')
        return value.strip()
    def endpoint(name):
        value = field(name).rstrip('/')
        try:
            url = urlsplit(value)
            valid = (url.scheme in ('https', 'http') and url.hostname and not
                     (url.username or url.password or url.query or url.fragment) and
                     not any(c.isspace() or c in '{}<>' for c in value) and
                     not url.path.endswith('/chat/completions'))
            url.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('请填写有效的接口根地址，不含密码、查询参数或 /chat/completions')
        return value
    provider = field('provider')
    name = field('name')
    if provider not in {'deepseek', 'qwen'} or not name or len(name)>60 or any(ord(c)<32 or ord(c)==127 for c in name):
        raise ValueError('请选择 DeepSeek / Qwen；显示名称须为 1–60 个字符，不含换行或控制字符')
    model, key = field('model'), field('api_key')
    if not model or any(c.isspace() for c in model) or not key:
        raise ValueError('请填写模型 ID 和 API Key')
    stream = normalize_stream_mode(data.get('stream', 'false'))
    config = dict(LCR_LLM_MODEL=model, LCR_LLM_TOKEN=key, LCR_LLM_BASE_URL=endpoint('base_url'),
                  LCR_LLM_STREAM=stream)
    return name, config


def profile_label(path):
    label_path=path.with_suffix('.label')
    if label_path.is_file() and not label_path.is_symlink():
        label=label_path.read_text(encoding='utf-8').strip()
        if label and len(label)<=60 and not any(ord(c)<32 or ord(c)==127 for c in label):
            return label
    if path.name=='model.local.json':
        return '默认模型'
    name = path.name.removeprefix('model.').removesuffix('.local.json')
    provider, separator, display = name.partition('.')
    # Earlier UI encoded provider and user-entered name as separate filename parts.
    if separator and provider in {'qwen', 'deepseek'} and display:
        return display
    return name


def create_profile(dashboard, data):
    label, config = resolve_profile(dashboard, data)
    with dashboard.lock:
        if any(p['label']==label for p in dashboard.profiles() if not p.get('internal')):
            raise ValueError('显示名称已存在，请换一个便于区分的名称')
        filename = f'model.{data["provider"].strip()}.{uuid.uuid4().hex}.local.json'
        dashboard.configs.mkdir(parents=True, exist_ok=True)
        path=dashboard.configs/filename
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as handle:
                json.dump(config,handle,ensure_ascii=False,indent=2)
                handle.write('\n')
            label_fd=os.open(path.with_suffix('.label'),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(label_fd,'w',encoding='utf-8') as handle:
                handle.write(label)
            from deployment.model_storage import persist_profile
            persist_profile(dashboard.root, filename)
        except Exception:
            path.unlink(missing_ok=True)
            path.with_suffix('.label').unlink(missing_ok=True)
            raise
    return {'id': filename}


def test_connection(dashboard, data):
    """Small real requests only on explicit click; no retries, response bodies or secrets returned."""
    from concurrent.futures import ThreadPoolExecutor
    if not isinstance(data, dict):
        raise ValueError('请求必须是对象')
    if 'profile_id' in data:
        profile = data['profile_id']
        if profile not in {p['id'] for p in dashboard.profiles()}:
            raise ValueError('模型配置不存在')
        config = dashboard.environment(profile)
    else:
        _, config = resolve_profile(dashboard, data)
    roles = [('模型接口', 'LCR_LLM_BASE_URL', 'LCR_LLM_TOKEN', 'LCR_LLM_MODEL')]
    with ThreadPoolExecutor(max_workers=1) as pool:
        futures = [pool.submit(_probe_model, config, *args) for args in roles]
        return {'results': [future.result() for future in futures],
                'note': '仅验证鉴权与基础文本请求，不代表工具调用、长上下文或完整测评已通过。未修改或保存配置。'}


def _probe_model(config, role, base_key, token_key, model_key):
    import time
    import httpx
    started = time.monotonic()
    result = {'role': role, 'status': 'failed'}
    try:
        result.update(_send_probe(config, base_key, token_key, model_key))
    except httpx.TimeoutException:
        result['message'] = '请求超时（网络读写时限 20 秒），未自动重试'
    except httpx.RequestError:
        result['message'] = '网络连接失败，请检查地址、网络、代理及证书'
    except (ValueError, TypeError, KeyError):
        result['message'] = '接口返回内容无法解析为兼容响应'
    finally:
        result['elapsed_seconds'] = round(time.monotonic() - started, 2)
    return result


def _send_probe(config, base_key, token_key, model_key):
    import httpx
    base, token, model = [config.get(k, '') for k in (base_key, token_key, model_key)]
    if not all(isinstance(v, str) and v.strip() for v in (base, token, model)):
        return {'message': '接口地址、模型名或密钥未配置完整'}
    url = urlsplit(base)
    if url.scheme not in ('https', 'http') or not url.hostname or url.username or url.password or url.query or url.fragment:
        return {'message': '接口根地址格式无效'}
    streaming = normalize_stream_mode(config.get('LCR_LLM_STREAM', 'false')) == 'true'
    # No source code is sent. Keep provider-specific thinking/tool settings unchanged.
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        request = client.build_request('POST', base.rstrip('/') + '/chat/completions',
            headers={'Authorization': 'Bearer ' + token},
            json={'model': model, 'messages': [{'role': 'user', 'content': 'Reply only OK.'}],
                  'max_tokens': 32, 'stream': streaming})
        response = client.send(request, stream=streaming)
        try:
            return _interpret_probe_response(response, streaming)
        finally:
            response.close()


def _interpret_probe_response(response, streaming):
    status = response.status_code
    if status != 200:
        messages = {400: '请求参数不被接受，请核对模型及流式参数支持',
                    401: '鉴权失败，请检查 API Key', 403: '没有接口或模型访问权限',
                    404: '地址或模型不存在，请检查 Base URL 和模型 ID',
                    429: '请求受限或额度不足，请查看服务端额度与限流',
                    502: '模型网关异常', 503: '模型服务暂不可用', 504: '模型网关超时'}
        return {'http_status': status,
                'message': messages.get(status, '接口返回非成功状态，请检查服务配置')}
    body = _collect_stream_probe(response) if streaming else response.json()
    choices = body.get('choices') if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return {'http_status': status, 'message': 'HTTP 成功，但返回格式不是兼容的 Chat Completions 响应'}
    choice = choices[0]
    message = choice.get('message') or {}
    content = message.get('content') if isinstance(message, dict) else None
    if isinstance(content, str) and content.strip() and choice.get('finish_reason') == 'stop':
        return {'status': 'passed', 'http_status': status, 'message': '连通成功，模型已正常返回文本'}
    return {'status': 'reachable', 'http_status': status,
            'message': '接口已响应，但未收到完整文本；可能是思考或输出上限限制，需单例验证'}


def _collect_stream_probe(response):
    import json
    content = []
    finish = None
    for line in response.iter_lines():
        if not line or not line.startswith('data:'):
            continue
        data = line[5:].strip()
        if data == '[DONE]':
            break
        chunk = json.loads(data)
        choices = chunk.get('choices') or []
        if choices:
            delta = choices[0].get('delta') or {}
            if isinstance(delta.get('content'), str):
                content.append(delta['content'])
            finish = choices[0].get('finish_reason') or finish
    return {'choices': [{'message': {'content': ''.join(content)}, 'finish_reason': finish}]}


def compose_roles(dashboard, review_id, judge_id, needs_judge=True):
    """Resolve independent model entries into the existing runner's role fields."""
    profiles = {p['id']: p for p in dashboard.profiles() if not p.get('internal')}
    def selected(identifier, legacy=False):
        if not isinstance(identifier, str):
            raise ValueError('请选择模型配置')
        source = identifier.removeprefix('legacy-judge:') if legacy else identifier
        if source not in profiles:
            raise ValueError('所选模型配置不存在')
        env = dashboard.environment(source)
        keys = ('JUDGE_BASE_URL','JUDGE_API_KEY','JUDGE_MODEL') if legacy else ('LCR_LLM_BASE_URL','LCR_LLM_TOKEN','LCR_LLM_MODEL')
        values = [env.get(k, '') for k in keys]
        if not all(isinstance(v, str) and v.strip() for v in values):
            raise ValueError('所选模型的地址、模型名或密钥未配置完整')
        return values
    review = selected(review_id)
    judge = selected(judge_id, isinstance(judge_id,str) and judge_id.startswith('legacy-judge:')) if needs_judge else ('','','')
    stream_env = dashboard.environment(review_id).get('LCR_LLM_STREAM', 'false')
    return dict(zip(('LCR_LLM_BASE_URL','LCR_LLM_TOKEN','LCR_LLM_MODEL','JUDGE_BASE_URL','JUDGE_API_KEY','JUDGE_MODEL'), (*review,*judge)), LCR_LLM_STREAM=stream_env, JUDGE_USE_MOCK='false')


def save_run_profile(dashboard, config):
    import uuid
    filename = 'model.run-' + uuid.uuid4().hex + '.local.json'
    dashboard.configs.mkdir(parents=True,exist_ok=True)
    fd=os.open(dashboard.configs/filename,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as handle:
            json.dump(config,handle,ensure_ascii=False,indent=2)
        from deployment.model_storage import persist_profile
        persist_profile(dashboard.root, filename)
    except Exception:
        (dashboard.configs / filename).unlink(missing_ok=True)
        raise
    return filename
