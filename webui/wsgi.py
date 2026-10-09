"""Server-only WSGI application, behind an authenticated reverse proxy."""
import os
import secrets
import subprocess
import tempfile
import zipfile
from urllib.parse import urlsplit
from pathlib import Path
from flask import Flask, request, jsonify, Response, send_file, g
from .queue import DurableDashboard

from webui.routes import is_page_route

STATIC=Path(__file__).parent/'static'

def create_app(dashboard=None):
    public=os.environ.get('BENCH_PUBLIC_URL','').rstrip('/')
    parsed=urlsplit(public)
    proxy=os.environ.get('BENCH_PROXY_TOKEN','')
    csrf=os.environ.get('BENCH_CSRF_TOKEN','')
    if parsed.scheme!='https' or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise RuntimeError('BENCH_PUBLIC_URL 必须为 HTTPS 站点根地址')
    mode = os.environ.get('BENCH_AUTH_MODE', 'proxy')
    basic_user = os.environ.get('BENCH_BASIC_USER', '')
    basic_password = os.environ.get('BENCH_BASIC_PASSWORD', '')
    if mode not in {'proxy', 'basic'}:
        raise RuntimeError('BENCH_AUTH_MODE 须为 proxy 或 basic')
    if len(csrf)<32 or (mode == 'proxy' and len(proxy)<32):
        raise RuntimeError('请设置至少 32 字符的 CSRF Token；代理模式还需代理校验密钥')
    if mode == 'basic' and (not basic_user or not basic_password):
        raise RuntimeError('试运行须设置 BENCH_BASIC_USER 和 BENCH_BASIC_PASSWORD')
    app=Flask(__name__,static_folder=None);app.config['MAX_CONTENT_LENGTH']=16384
    dashboard=dashboard or DurableDashboard()
    @app.before_request
    def authorize():
        if request.path == '/healthz' and request.method == 'GET':
            return None
        if request.host != parsed.netloc:
            return jsonify(error='请求域名与 BENCH_PUBLIC_URL 不一致'),403
        if mode == 'basic':
            auth = request.authorization
            if (not auth or auth.type.lower() != 'basic'
                    or not secrets.compare_digest((auth.username or '').encode(), basic_user.encode())
                    or not secrets.compare_digest((auth.password or '').encode(), basic_password.encode())):
                response = jsonify(error='请登录测评工作台')
                response.status_code = 401
                response.headers['WWW-Authenticate'] = 'Basic realm="Review Lab", charset="UTF-8"'
                return response
            g.bench_user, g.bench_role = basic_user, 'operator'
        else:
            # The gateway must overwrite identity headers, never forward browser values.
            if not secrets.compare_digest(request.headers.get('X-Bench-Proxy-Token',''),proxy):
                return jsonify(error='请求未通过认证入口'),403
            user, role = request.headers.get('X-Bench-User'), request.headers.get('X-Bench-Role')
            if not user or role not in {'viewer','operator'}:
                return jsonify(error='需要有效用户与角色'),403
            g.bench_user, g.bench_role = user, role
        if request.path == '/api/cases/upload':
            from .case_upload import limits
            request.max_content_length = limits()['upload_max_bytes'] + 1024 * 1024
        origin=request.headers.get('Origin')
        if origin and origin!=public:return jsonify(error='不接受跨站请求'),403
        if request.method=='POST':
            if g.bench_role!='operator':return jsonify(error='只读用户不能执行或停止任务'),403
            if origin!=public:return jsonify(error='缺少可信页面来源'),403
        if request.path.startswith('/api/') and not secrets.compare_digest(request.headers.get('X-Dashboard-Token',''),csrf):
            return jsonify(error='页面会话失效，请刷新'),403
    @app.after_request
    def headers(response):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response
    @app.errorhandler(ValueError)
    def invalid(exc):return jsonify(error=dashboard.redact(str(exc))),400
    @app.errorhandler(KeyError)
    @app.errorhandler(OSError)
    def unavailable(exc):return jsonify(error='文件或参数不可用，请检查记录'),400
    @app.errorhandler(413)
    def too_large(exc):
        from .case_upload import limits
        message = '案例 ZIP 上限为 ' + limits()['upload_max_label'] if request.path == '/api/cases/upload' else '请求过大，JSON 请求上限为 16 KB'
        return jsonify(error=message),413
    @app.post('/api/cases/upload')
    def upload_cases():
        if os.environ.get('BENCH_CASE_UPLOAD') != '1':
            return jsonify(error='当前部署未启用网页案例上传'),404
        from .case_upload import receive_archive
        if request.mimetype == 'application/zip':
            stream, length = request.stream, request.content_length
        elif request.mimetype == 'multipart/form-data':
            upload = request.files.get('archive')
            if upload is None or not upload.filename or not upload.filename.lower().endswith('.zip'):
                raise ValueError('请选择 ZIP 格式的案例集包')
            stream, length = upload.stream, None
        else:
            raise ValueError('请选择 ZIP 文件上传')
        from .case_assets import install
        with tempfile.TemporaryDirectory(prefix='case-upload-', dir=dashboard.data) as folder:
            archive = Path(folder) / 'cases.zip'
            receive_archive(stream, archive, length)
            try:
                return jsonify(install(dashboard, archive))
            except (zipfile.BadZipFile, RuntimeError):
                raise ValueError('ZIP 无效或需要解密，请检查文件格式') from None
            except subprocess.CalledProcessError:
                raise ValueError('案例 Git 历史准备失败，请查看服务器日志；可以重新上传') from None
    @app.get('/healthz')
    def health():
        import time
        ready = os.environ.get('BENCH_CONTAINER_ROLE', 'web') != 'all' or (
            time.time() - (dashboard.queue.meta('heartbeat') or 0) < 15
            and not dashboard.queue.meta('recovery_required'))
        return jsonify(status='ready' if ready else 'worker_unavailable'), 200 if ready else 503
    @app.get('/')
    def index():return Response((STATIC/'index.html').read_text(encoding='utf-8').replace('__TOKEN__',csrf),mimetype='text/html')
    @app.get('/<path:name>')
    def static(name):
        if is_page_route('/'+name):return index()
        if name not in {'app.js','presentation.js','style.css'}:return '',404
        return send_file(STATIC/name)
    @app.get('/api/state')
    def state():
        value=dashboard.snapshot();value['access_role']=g.bench_role;return jsonify(value)
    @app.get('/api/log')
    def log():return jsonify(dashboard.logs(request.args['job']))
    @app.get('/api/comparison-case')
    def comparison_case():
        from .experiment_view import case_evidence
        return jsonify(case_evidence(dashboard,request.args['key'],request.args['group'],request.args['case']))
    @app.get('/api/artifact')
    def artifact():return jsonify(dashboard.artifact(request.args['key'],request.args['path']))
    @app.get('/api/export')
    def export():
        root=dashboard.run_dir(request.args['key']);path=root.parent/(root.name+'-results.tar.gz')
        if not path.is_file() or path.is_symlink():raise ValueError('请先生成结果归档')
        return send_file(path,as_attachment=True,download_name=path.name)
    @app.post('/api/comparisons')
    def comparison():
        from .experiment_view import submit_named
        data=request.get_json()
        if not isinstance(data,dict):raise ValueError('请求必须是对象')
        data['_actor']=g.bench_user[:200]
        return jsonify(submit_named(dashboard,data,comparison=True)),202
    @app.post('/api/profiles/test')
    def test_profile():
        from .profiles import test_connection
        return jsonify(test_connection(dashboard, request.get_json()))
    @app.post('/api/profiles')
    def profiles():
        from .profiles import create_profile
        return jsonify(create_profile(dashboard, request.get_json())),201
    @app.post('/api/jobs')
    def jobs():
        data=request.get_json()
        if not isinstance(data,dict):raise ValueError('请求必须为对象')
        data['_actor']=g.bench_user[:200]
        from .experiment_view import submit_named
        return jsonify(submit_named(dashboard,data)),202
    @app.post('/api/stop')
    def stop():
        data=request.get_json()
        if not isinstance(data,dict):raise ValueError('请求必须为对象')
        dashboard.stop(data['job'],actor=g.bench_user[:200]);return jsonify(ok=True)
    return app
