"""Loopback HTTP transport for the same Flask application used in production."""
import argparse
import os
import secrets
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote
from wsgiref.handlers import SimpleHandler
from runner.locking import acquire as lock_instance_file
from runner.runtime import data_root
from .service import Dashboard, ROOT


def _environ(handler):
    parsed = urlsplit(handler.path)
    environ = {
        'REQUEST_METHOD': handler.command, 'SCRIPT_NAME': '',
        'PATH_INFO': unquote(parsed.path, encoding='iso-8859-1'),
        'QUERY_STRING': parsed.query, 'SERVER_NAME': '127.0.0.1',
        'SERVER_PORT': str(handler.server.server_port),
        'SERVER_PROTOCOL': handler.request_version,
        'REMOTE_ADDR': handler.client_address[0],
    }
    for name, value in handler.headers.items():
        key = name.upper().replace('-', '_')
        if key not in {'CONTENT_TYPE', 'CONTENT_LENGTH'}:
            key = 'HTTP_' + key
        environ[key] = value
    return environ


def handler_for(dashboard, token):
    from .wsgi import create_app
    application = create_app(dashboard, local_token=token)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            # Suppress the default per-request access log; WSGI errors still go to stderr.
            pass

        def dispatch(self):
            self.close_connection = True
            transport = SimpleHandler(self.rfile, self.wfile, sys.stderr,
                                      _environ(self), multithread=True)
            transport.run(application)

        do_GET = dispatch
        do_HEAD = dispatch
        do_POST = dispatch
        do_PUT = dispatch
        do_DELETE = dispatch
        do_OPTIONS = dispatch

    return Handler


def main():
    parser=argparse.ArgumentParser(description='本地测评工作台，默认仅监听 127.0.0.1')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-open',action='store_true')
    upload=parser.add_mutually_exclusive_group()
    upload.add_argument('--case-upload',dest='case_upload',action='store_true',help='启用与行内工作台相同的案例 ZIP 上传功能（默认）')
    upload.add_argument('--no-case-upload',dest='case_upload',action='store_false',help='关闭案例 ZIP 上传入口')
    parser.set_defaults(case_upload=None)
    args=parser.parse_args()
    if os.getenv("BENCH_SERVER_MODE"):
        parser.error("服务器模式请使用部署文档中的 Gunicorn 与独立 Worker，不使用本地 API 入口")
    if args.case_upload is not None:
        os.environ['BENCH_CASE_UPLOAD']='1' if args.case_upload else '0'
    else:
        os.environ.setdefault('BENCH_CASE_UPLOAD', '1')
    if not 1<=args.port<=65535:
        parser.error('端口应为 1–65535')
    state=data_root(ROOT)/'ui-state';state.mkdir(parents=True, exist_ok=True)
    with (state/'server.lock').open('a+b') as lock:
        try:
            lock_instance_file(lock)
        except (BlockingIOError, OSError):
            parser.error('本项目的页面服务已经运行，请打开原页面')
        app=Dashboard()
        try:
            server=ThreadingHTTPServer(('127.0.0.1',args.port),handler_for(app,secrets.token_urlsafe(32)))
        except OSError:
            parser.error('端口不可用；可用 --port 其他端口 启动')
        url=f'http://127.0.0.1:{args.port}'
        print(f'测评工作台：{url}\n统一启动入口：项目根目录 npm run dev / npm start。关闭本服务会停止当前任务；日志在数据目录 ui-state/jobs/。',flush=True)
        def stop_signal(signum, frame):
            raise KeyboardInterrupt
        for sig in (getattr(signal, 'SIGTERM', None), getattr(signal, 'SIGHUP', None), getattr(signal, 'SIGBREAK', None)):
            if sig is None:continue
            signal.signal(sig, stop_signal)
        try:
            server.serve_forever(poll_interval=.3)
        except KeyboardInterrupt:
            print('\n正在停止当前任务并保存日志…',flush=True)
        finally:
            app.shutdown()
            server.server_close()
