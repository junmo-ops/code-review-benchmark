from webui.routes import is_page_route
import argparse
import os
from runner.locking import acquire as lock_instance_file
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import signal
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser
from .service import Dashboard, ROOT

STATIC = Path(__file__).parent/'static'


def handler_for(app, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, data, content_type='application/json; charset=utf-8', download=None):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if download:
                self.send_header('Content-Disposition', 'attachment; filename="'+download+'"')
            self.end_headers()
            self.wfile.write(data)

        def authorized(self, mutation=False):
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in hosts:
                self.send(403, {'error':'只接受本机访问'})
                return False
            origin = self.headers.get('Origin')
            if origin and origin not in {'http://'+h for h in hosts}:
                self.send(403, {'error':'不接受跨站请求'})
                return False
            if mutation or urlsplit(self.path).path.startswith('/api/'):
                if not secrets.compare_digest(self.headers.get('X-Dashboard-Token',''), token):
                    self.send(403, {'error':'页面会话失效，请刷新'})
                    return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            try:
                parsed = urlsplit(self.path)
                q = {k:v[0] for k,v in parse_qs(parsed.query).items()}
                if is_page_route(parsed.path):
                    data = (STATIC/'index.html').read_text(encoding='utf-8').replace('__TOKEN__',token).encode()
                    return self.send(200, data, 'text/html; charset=utf-8')
                if parsed.path == '/favicon.ico':
                    return self.send(204, b'', 'image/x-icon')
                if parsed.path in {'/app.js','/presentation.js','/style.css'}:
                    path = STATIC/parsed.path[1:]
                    return self.send(200, path.read_bytes(), 'text/javascript; charset=utf-8' if path.suffix=='.js' else 'text/css; charset=utf-8')
                if parsed.path == '/api/state':
                    return self.send(200, app.snapshot())
                if parsed.path == '/api/log':
                    return self.send(200, app.logs(q['job']))
                if parsed.path == '/api/comparison-case':
                    from .experiment_view import case_evidence
                    return self.send(200,case_evidence(app,q['key'],q['group'],q['case']))
                if parsed.path == '/api/artifact':
                    return self.send(200, app.artifact(q['key'],q['path']))
                if parsed.path == '/api/export':
                    root=app.run_dir(q['key'])
                    path=root.parent/(root.name+'-results.tar.gz')
                    if not path.is_file() or path.is_symlink():
                        raise ValueError('请先生成结果归档')
                    return self.send(200, path.read_bytes(), 'application/gzip', root.name+'-results.tar.gz')
                self.send(404, {'error':'页面不存在'})
            except (ValueError, KeyError, OSError) as exc:
                self.send(400, {'error':app.redact(str(exc)) if isinstance(exc,ValueError) else '文件不可用，请检查批次记录'})
            except Exception:
                self.send(500, {'error':'读取失败，请查看页面服务终端'})

        def do_POST(self):
            if not self.authorized(mutation=True):
                return
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 16384:
                    raise ValueError('请求大小不合法')
                if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                    raise ValueError('请求必须为 JSON')
                request = json.loads(self.rfile.read(length))
                if not isinstance(request,dict):
                    raise ValueError('请求格式错误')
                if self.path == '/api/comparisons':
                    from .experiment_view import submit_named
                    return self.send(202, submit_named(app, request, comparison=True))
                if self.path == '/api/profiles/test':
                    from .profiles import test_connection
                    return self.send(200, test_connection(app, request))
                if self.path == '/api/profiles':
                    from .profiles import create_profile
                    return self.send(201, create_profile(app, request))
                if self.path == '/api/jobs':
                    from .experiment_view import submit_named
                    return self.send(202, submit_named(app, request))
                if self.path == '/api/stop':
                    app.stop(request['job'])
                    return self.send(200, {'ok':True})
                self.send(404, {'error':'操作不存在'})
            except (ValueError, KeyError, OSError) as exc:
                self.send(400, {'error':app.redact(str(exc)) if isinstance(exc,ValueError) else '操作参数或文件不可用'})
            except Exception:
                self.send(500, {'error':'操作未完成，请查看日志'})
    return Handler


def main():
    parser=argparse.ArgumentParser(description='本地测评工作台，默认仅监听 127.0.0.1')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-open',action='store_true')
    args=parser.parse_args()
    if os.getenv("BENCH_SERVER_MODE"):
        parser.error("服务器模式请使用部署文档中的 Gunicorn 与独立 Worker，不使用 ui.py")
    if not 1<=args.port<=65535:
        parser.error('端口应为 1–65535')
    state=ROOT/'ui-state';state.mkdir(exist_ok=True)
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
        print(f'测评工作台：{url}\n关闭浏览器不会停止任务；关闭本服务会停止当前任务。日志保存在 ui-state/jobs/。',flush=True)
        if not args.no_open:
            threading.Timer(.5,lambda:webbrowser.open(url)).start()
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
