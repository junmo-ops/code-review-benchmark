"""Server-only WSGI application, behind an authenticated reverse proxy."""
import os
import secrets
import subprocess
import tempfile
import traceback
import zipfile
from urllib.parse import urlsplit
from pathlib import Path
from flask import Flask, request, jsonify, send_file, g
from .queue import DurableDashboard
from runner.paths import PROJECT_ROOT




def _authenticate_identity(public, proxy, mode, basic_user, basic_password):
    if mode == 'basic':
        auth = request.authorization
        valid = (auth and auth.type.lower() == 'basic'
                 and secrets.compare_digest((auth.username or '').encode(), basic_user.encode())
                 and secrets.compare_digest((auth.password or '').encode(), basic_password.encode()))
        if not valid:
            response = jsonify(error='请登录测评工作台')
            response.status_code = 401
            response.headers['WWW-Authenticate'] = 'Basic realm="Review Lab", charset="UTF-8"'
            return response
        g.bench_user, g.bench_role = basic_user, 'operator'
    else:
        if not secrets.compare_digest(request.headers.get('X-Bench-Proxy-Token',''), proxy):
            return jsonify(error='请求未通过认证入口'), 403
        user, role = request.headers.get('X-Bench-User'), request.headers.get('X-Bench-Role')
        if not user or role not in {'viewer','operator'}:
            return jsonify(error='需要有效用户与角色'), 403
        g.bench_user, g.bench_role = user, role
    return None


def _set_upload_limit():
    if request.path in {'/api/cases/upload', '/api/repositories/upload'}:
        from .case_upload import limits
        request.max_content_length = limits()['upload_max_bytes'] + 1024 * 1024
    elif request.path.startswith('/api/uploads/') and '/chunks/' in request.path:
        from .resumable_uploads import CHUNK_SIZE
        request.max_content_length = CHUNK_SIZE


def _authorize_origin(public, csrf):
    origin = request.headers.get('Origin')
    if origin and origin != public:
        return jsonify(error='不接受跨站请求'), 403
    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and (g.bench_role != 'operator' or origin != public):
        message = '只读用户不能修改工作台配置或任务' if g.bench_role != 'operator' else '缺少可信页面来源'
        return jsonify(error=message), 403
    if request.path.startswith('/api/') and request.path != '/api/session' and not secrets.compare_digest(request.headers.get('X-Dashboard-Token',''), csrf):
        return jsonify(error='页面会话失效，请刷新'), 403
    return None


def _authorize_request(public, parsed, proxy, csrf, mode, basic_user, basic_password):
    if request.path in {'/healthz', '/readyz'} and request.method == 'GET':
        return None
    if request.host != parsed.netloc:
        return jsonify(error='请求域名与 BENCH_PUBLIC_URL 不一致'), 403
    response = _authenticate_identity(public, proxy, mode, basic_user, basic_password)
    if response:
        return response
    _set_upload_limit()
    return _authorize_origin(public, csrf)


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
    frontend_root=Path(os.environ.get('BENCH_FRONTEND_DIST', PROJECT_ROOT/'frontend-dist')).resolve()
    pages={'/','/jobs','/experiments','/comparisons','/models','/cases','/repositories','/guide'}
    dashboard=dashboard or DurableDashboard()
    @app.before_request
    def authorize():
        # The gateway must overwrite identity headers, never forward browser values.
        return _authorize_request(public, parsed, proxy, csrf, mode, basic_user, basic_password)
    @app.after_request
    def headers(response):
        response.headers['Cache-Control']='public, max-age=31536000, immutable' if getattr(g,'frontend_asset',False) else 'no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        if getattr(g,'frontend_index',False):
            nonce=g.frontend_nonce
            response.headers['Content-Security-Policy']=(f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; "
                f"style-src 'self' 'nonce-{nonce}'; style-src-attr 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        else:response.headers['Content-Security-Policy'] = "default-src 'none'; frame-ancestors 'none'"
        return response
    @app.errorhandler(404)
    @app.errorhandler(405)
    def missing_route(exc):
        return jsonify(error='接口或请求方法不存在'), exc.code
    @app.errorhandler(ValueError)
    def invalid(exc):return jsonify(error=dashboard.redact(str(exc))),400
    @app.errorhandler(KeyError)
    @app.errorhandler(OSError)
    def unavailable(exc):
        details = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        app.logger.error('请求处理失败：%s %s\n%s', request.method, request.path,
                         dashboard.redact(details))
        return jsonify(error='文件或参数不可用，请检查记录'),400
    @app.errorhandler(413)
    def too_large(exc):
        from .case_upload import limits
        if request.path in {'/api/cases/upload', '/api/repositories/upload'}:
            message = '上传 ZIP 上限为 ' + limits()['upload_max_label']
        else:
            message = '请求过大，JSON 请求上限为 16 KB'
        return jsonify(error=message),413
    @app.get('/', defaults={'path':''})
    @app.get('/<path:path>')
    def frontend(path):
        normalized='/'+path.rstrip('/') if path else '/'
        if normalized in pages:
            index=frontend_root/'index.html'
            if not index.is_file():return jsonify(error='前端页面尚未构建'),503
            nonce=secrets.token_urlsafe(18);g.frontend_index=True;g.frontend_nonce=nonce
            return app.response_class(index.read_text(encoding='utf-8').replace('__STYLE_NONCE__',nonce),mimetype='text/html')
        if path.startswith('assets/'):
            target=(frontend_root/path).resolve()
            try:target.relative_to(frontend_root)
            except ValueError:return jsonify(error='文件不存在'),404
            if not target.is_file():return jsonify(error='文件不存在'),404
            g.frontend_asset=True
            return send_file(target,conditional=True)
        return jsonify(error='页面不存在'),404
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
        from .case_import import submit
        with tempfile.TemporaryDirectory(prefix='case-upload-', dir=dashboard.data) as folder:
            archive = Path(folder) / 'cases.zip'
            receive_archive(stream, archive, length)
            return jsonify(submit(dashboard, archive)),202
    @app.get('/api/cases/imports')
    def case_import_status():
        from .case_import import current
        return jsonify(task=current(dashboard))
    @app.post('/api/uploads/start')
    def start_upload():
        if os.environ.get('BENCH_CASE_UPLOAD') != '1':
            return jsonify(error='当前部署未启用案例资源上传'),404
        from .resumable_uploads import create
        return jsonify(create(dashboard, request.get_json(silent=True))), 201
    @app.get('/api/uploads/<upload_id>')
    def upload_status(upload_id):
        from .resumable_uploads import status
        return jsonify(status(dashboard, upload_id))
    @app.get('/api/uploads/current')
    def current_upload():
        from .resumable_uploads import current
        return jsonify(current(dashboard, request.args.get('kind', '')))
    @app.post('/api/uploads/<upload_id>/chunks/<int:index>')
    def upload_chunk(upload_id, index):
        if os.environ.get('BENCH_CASE_UPLOAD') != '1':
            return jsonify(error='当前部署未启用案例资源上传'),404
        if request.mimetype != 'application/octet-stream':
            raise ValueError('上传分块格式无效')
        from .resumable_uploads import put_chunk
        result = put_chunk(dashboard, upload_id, index, request.stream, request.content_length)
        return jsonify(result)
    @app.post('/api/uploads/<upload_id>/complete')
    def finish_upload(upload_id):
        if os.environ.get('BENCH_CASE_UPLOAD') != '1':
            return jsonify(error='当前部署未启用案例资源上传'),404
        from .resumable_uploads import complete
        return jsonify(complete(dashboard, upload_id)), 202
    @app.post('/api/uploads/<upload_id>/abort')
    def cancel_upload(upload_id):
        if os.environ.get('BENCH_CASE_UPLOAD') != '1':
            return jsonify(error='当前部署未启用案例资源上传'),404
        from .resumable_uploads import abort
        return jsonify(abort(dashboard, upload_id))
    @app.post('/api/repositories/upload')
    def upload_repositories():
        if os.environ.get('BENCH_CASE_UPLOAD') != '1':
            return jsonify(error='当前部署未启用案例资源上传'),404
        if request.mimetype != 'application/zip':
            raise ValueError('请选择 ZIP 格式的仓库资源包')
        from .case_upload import receive_archive
        from .case_assets import submit_repository_upload
        with tempfile.TemporaryDirectory(prefix='repo-upload-', dir=dashboard.data) as folder:
            archive = Path(folder) / 'repositories.zip'
            receive_archive(request.stream, archive, request.content_length)
            try:
                status, result = submit_repository_upload(dashboard, archive)
                return jsonify(result), status
            except (zipfile.BadZipFile, RuntimeError):
                raise ValueError('仓库 ZIP 无效，请检查文件格式') from None
            except subprocess.CalledProcessError:
                raise ValueError('Git bundle 校验失败，请查看服务器日志') from None
    @app.post('/api/cases/manage')
    def manage_cases():
        if os.environ.get('BENCH_CASE_UPLOAD') != '1':
            return jsonify(error='当前工作台未开启案例集管理'), 404
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError('案例集操作参数无效')
        from .case_assets import manage_case_dataset
        return jsonify(manage_case_dataset(dashboard, payload))
    @app.post('/api/repositories/manage')
    def manage_repositories():
        if os.environ.get('BENCH_CASE_UPLOAD') != '1':
            return jsonify(error='当前工作台未开启仓库管理'), 404
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError('仓库操作参数无效')
        from .case_assets import manage_repository
        return jsonify(manage_repository(dashboard, payload))
    @app.post('/api/recovery')
    def acknowledge_recovery():
        value = request.get_json()
        if not isinstance(value, dict) or value.get('confirmed') is not True:
            raise ValueError('请先确认旧任务进程和容器已停止')
        dashboard.acknowledge_recovery(actor=g.bench_user[:200])
        return jsonify(ok=True)
    @app.get('/healthz')
    def health():
        return jsonify(status='alive'), 200

    @app.get('/readyz')
    def readiness():
        import time
        ready = os.environ.get('BENCH_CONTAINER_ROLE', 'web') != 'all' or time.time() - (dashboard.queue.meta('heartbeat') or 0) < 15
        status = 'recovery_required' if dashboard.queue.meta('recovery_required') else 'ready'
        return jsonify(status=status if ready else 'worker_unavailable'), 200 if ready and status == 'ready' else 503
    @app.get('/api/session')
    def session():
        return jsonify(csrf_token=csrf, access_role=g.bench_role)
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
    @app.put('/api/profiles/<profile_id>')
    def update_model_profile(profile_id):
        from .profiles import update_profile
        return jsonify(update_profile(dashboard, profile_id, request.get_json()))
    @app.delete('/api/profiles/<profile_id>')
    def delete_model_profile(profile_id):
        from .profiles import delete_profile
        return jsonify(delete_profile(dashboard, profile_id))
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
    from .case_import import start
    start(dashboard)
    from runner.diagnostics import install_request_diagnostics
    install_request_diagnostics(app)
    return app
