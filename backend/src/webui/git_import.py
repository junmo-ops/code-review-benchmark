"""Import complete Git histories through the existing immutable resource pipeline."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit
import zipfile


def validate_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError('请输入行内 Git HTTP(S) 地址')
    url = urlsplit(value)
    if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('请使用不含账号、密码和查询参数的 Git HTTP(S) 地址')
    name = url.path.rstrip('/').removesuffix('.git').strip('/').split('/')
    if len(name) < 2 or not all(re.fullmatch(r'[A-Za-z0-9_.-]+', part) for part in name[-2:]):
        raise ValueError('Git 地址必须包含组织和仓库名')
    return value.rstrip('/'), '/'.join(name[-2:])


def submit(dashboard, payload):
    if not isinstance(payload, dict):
        raise ValueError('Git 导入参数无效')
    url, name = validate_url(payload.get('url'))
    from .case_import import submit as enqueue
    with tempfile.TemporaryDirectory(dir=dashboard.data) as temporary:
        path = Path(temporary) / 'request.json'
        path.write_text(json.dumps({'url': url, 'name': name}), encoding='utf-8')
        return enqueue(dashboard, path, kind='repository_git')


def git(path, *args, auth_url=None):
    from .git_auth import environment, redact
    env = environment(auth_url)
    result = subprocess.run(['git', '-c', 'http.followRedirects=false',
                             '-C', str(path), *args], env=env, capture_output=True, timeout=3600)
    if result.returncode:
        detail = redact(result.stderr.decode(errors='replace'))[-2000:]
        raise ValueError(f'Git 拉取失败（退出码 {result.returncode}）：' + detail)


def prepare(dashboard, url, progress):
    cache = dashboard.data / 'git-import-cache' / hashlib.sha256(url.encode()).hexdigest()
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not (cache / '.git').is_dir():
        shutil.rmtree(cache, ignore_errors=True)
        progress('clone', '正在完整 clone 仓库（不使用浅克隆）')
        try:
            git(cache.parent, 'clone', '--no-checkout', '--no-recurse-submodules', url, str(cache), auth_url=url)
        except (ValueError, OSError, subprocess.TimeoutExpired):
            shutil.rmtree(cache, ignore_errors=True)
            raise
    else:
        progress('fetch', '正在 fetch 仓库最新历史')
    git(cache, 'fetch', '--tags', 'origin', '+refs/heads/*:refs/remotes/origin/*', auth_url=url)
    return cache


def install(dashboard, request_path, progress):
    from runner.git_bundles import create_complete_bundle
    from .case_assets import install_repositories, _hash_file
    request = json.loads(request_path.read_text())
    url, name = validate_url(request['url'])
    repo = prepare(dashboard, url, progress)
    slug = name.replace('/', '__')
    with tempfile.TemporaryDirectory(prefix='git-import-', dir=dashboard.data) as temporary:
        root = Path(temporary)
        bundle = root / 'history.bundle'
        progress('bundle', '正在校验并保存完整提交历史')
        create_complete_bundle(repo, bundle)
        key = f'bundles/{slug}.bundle'
        manifest = {'format': 'code-review-benchmark-repositories-v1',
                    'repositories': {slug: {'name': name}},
                    'files': {key: {'size': bundle.stat().st_size, 'sha256': _hash_file(bundle)}}}
        archive = root / 'resource.zip'
        with zipfile.ZipFile(archive, 'w', allowZip64=True) as output:
            output.write(bundle, key)
            output.writestr('manifest.json', json.dumps(manifest))
        result = install_repositories(dashboard, archive, background=True, progress=progress)
    return dict(result, message='Git 仓库已导入或更新：' + name)
