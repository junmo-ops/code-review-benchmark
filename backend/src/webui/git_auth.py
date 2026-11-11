"""Process-only HTTP authentication; never persist credentials in repository URLs."""
import base64
import os
import re
from urllib.parse import quote


def environment(url):
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never',
               GIT_ALLOW_PROTOCOL='http:https', GIT_LFS_SKIP_SMUDGE='1')
    username = env.get('BENCH_GIT_USERNAME', '')
    password = env.get('BENCH_GIT_PASSWORD', '')
    if bool(username) != bool(password):
        raise ValueError('请同时配置 BENCH_GIT_USERNAME 和 BENCH_GIT_PASSWORD')
    if username:
        if not url or ':' in username or any(c in username + password for c in '\r\n'):
            raise ValueError('Git 认证配置无效')
        token = base64.b64encode((username + ':' + password).encode()).decode()
        # URL scope prevents sending credentials to other HTTP destinations.
        env.update(GIT_CONFIG_COUNT='2', GIT_CONFIG_KEY_0='credential.helper',
                   GIT_CONFIG_VALUE_0='', GIT_CONFIG_KEY_1='http.' + url + '.extraHeader',
                   GIT_CONFIG_VALUE_1='Authorization: Basic ' + token)
    return env


def redact(message):
    username = os.environ.get('BENCH_GIT_USERNAME', '')
    password = os.environ.get('BENCH_GIT_PASSWORD', '')
    secrets = [password, quote(password, safe='')]
    if username and password:
        secrets.append(base64.b64encode((username + ':' + password).encode()).decode())
    for value in sorted(set(secrets), key=len, reverse=True):
        if value:
            message = message.replace(value, '<REDACTED>')
    message = re.sub(r'(?i)(https?://)[^\s/@]+@', r'\1<REDACTED>@', message)
    return re.sub(r'(?im)(authorization\s*[:=])[^\r\n]*', r'\1 <REDACTED>', message)
