"""Server-only WSGI application, behind an authenticated reverse proxy."""
import os
import secrets
from urllib.parse import urlsplit
from pathlib import Path
from flask import Flask, request, jsonify, Response, send_file
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
    if len(proxy)<32 or len(csrf)<32:raise RuntimeError('请设置至少 32 字符的代理校验密钥和 CSRF Token')
    app=Flask(__name__,static_folder=None);app.config['MAX_CONTENT_LENGTH']=16384
    dashboard=dashboard or DurableDashboard()
    @app.before_request
    def authorize():
        # Proxy overwrites these headers; never trust headers from a direct public client.
        if request.host!=parsed.netloc or not secrets.compare_digest(request.headers.get('X-Bench-Proxy-Token',''),proxy):
            return jsonify(error='请求未通过认证入口'),403
        if not request.headers.get('X-Bench-User') or request.headers.get('X-Bench-Role') not in {'viewer','operator'}:
            return jsonify(error='需要有效用户与角色'),403
        origin=request.headers.get('Origin')
        if origin and origin!=public:return jsonify(error='不接受跨站请求'),403
        if request.method=='POST':
            if request.headers.get('X-Bench-Role')!='operator':return jsonify(error='只读用户不能执行或停止任务'),403
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
    @app.get('/')
    def index():return Response((STATIC/'index.html').read_text().replace('__TOKEN__',csrf),mimetype='text/html')
    @app.get('/<path:name>')
    def static(name):
        if is_page_route('/'+name):return index()
        if name not in {'app.js','presentation.js','style.css'}:return '',404
        return send_file(STATIC/name)
    @app.get('/api/state')
    def state():
        value=dashboard.snapshot();value['access_role']=request.headers['X-Bench-Role'];return jsonify(value)
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
        data['_actor']=request.headers['X-Bench-User'][:200]
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
        data['_actor']=request.headers['X-Bench-User'][:200]
        from .experiment_view import submit_named
        return jsonify(submit_named(dashboard,data)),202
    @app.post('/api/stop')
    def stop():
        data=request.get_json()
        if not isinstance(data,dict):raise ValueError('请求必须为对象')
        dashboard.stop(data['job'],actor=request.headers['X-Bench-User'][:200]);return jsonify(ok=True)
    return app
