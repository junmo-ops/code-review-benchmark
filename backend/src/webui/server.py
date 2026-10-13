import argparse
import mimetypes
import os
from runner.locking import acquire as lock_instance_file
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import signal
from urllib.parse import parse_qs, urlsplit
import zipfile
from .service import Dashboard, ROOT
from runner.runtime import data_root



def handler_for(app, token):
    from .case_import import start
    start(app)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            # Suppress routine HTTP access logs so polling does not obscure task output.
            pass

        def send(self, status, data, content_type='application/json; charset=utf-8', download=None,
                 csp="default-src 'none'; frame-ancestors 'none'", cache='no-store'):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Security-Policy', csp)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', cache)
            self.send_header('X-Content-Type-Options', 'nosniff')
            if download:
                self.send_header('Content-Disposition', 'attachment; filename="'+download+'"')
            try:
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def authorized(self, mutation=False):
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in hosts:
                self.send(403, {'error':'只接受本机访问'})
                return False
            origin = self.headers.get('Origin')
            if origin and origin not in {'http://'+h for h in hosts}:
                self.send(403, {'error':'不接受跨站请求'})
                return False
            if mutation or (urlsplit(self.path).path.startswith('/api/') and urlsplit(self.path).path != '/api/session'):
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
                return self._dispatch_get(parsed, q)
            except (ValueError, KeyError, OSError) as exc:
                return self._handle_get_error(exc)
            except Exception:
                self.send(500, {'error':'读取失败，请查看页面服务终端'})

        def _dispatch_get(self, parsed, query):
            if parsed.path == '/api/session':
                return self.send(200, {'csrf_token': token, 'access_role': 'operator'})
            if parsed.path == '/api/state':
                return self.send(200, app.snapshot())
            if parsed.path == '/api/cases/imports':
                from .case_import import current
                return self.send(200, {'task': current(app)})
            if parsed.path == '/api/uploads/current':
                from .resumable_uploads import current
                return self.send(200, current(app, query.get('kind', '')))
            upload_status = re.fullmatch(r'/api/uploads/([a-f0-9]{32})', parsed.path)
            if upload_status:
                from .resumable_uploads import status
                return self.send(200, status(app, upload_status.group(1)))
            if parsed.path == '/api/log':
                return self.send(200, app.logs(query['job']))
            if parsed.path == '/api/comparison-case':
                from .experiment_view import case_evidence
                return self.send(200,case_evidence(app,query['key'],query['group'],query['case']))
            if parsed.path == '/api/artifact':
                return self.send(200, app.artifact(query['key'],query['path']))
            if parsed.path == '/api/export':
                root=app.run_dir(query['key'])
                path=root.parent/(root.name+'-results.tar.gz')
                if not path.is_file() or path.is_symlink():
                    raise ValueError('请先生成结果归档')
                return self.send(200, path.read_bytes(), 'application/gzip', root.name+'-results.tar.gz')
            if not parsed.path.startswith('/api/'):
                return self._frontend(parsed.path)
            return self.send(404, {'error':'页面不存在'})

        def _frontend(self, path):
            root=(ROOT/'frontend'/'dist').resolve()
            pages={'/','/jobs','/experiments','/comparisons','/models','/cases','/repositories','/guide'}
            normalized=path.rstrip('/') or '/'
            if normalized in pages:
                index=root/'index.html'
                if not index.is_file():return self.send(503,{'error':'前端页面尚未构建'})
                nonce=secrets.token_urlsafe(18)
                csp=(f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'nonce-{nonce}'; "
                     "style-src-attr 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
                     "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
                html=index.read_text(encoding='utf-8').replace('__STYLE_NONCE__',nonce).encode()
                return self.send(200,html,'text/html; charset=utf-8',csp=csp)
            if path.startswith('/assets/'):
                target=(root/path.lstrip('/')).resolve()
                try:target.relative_to(root)
                except ValueError:return self.send(404,{'error':'文件不存在'})
                if not target.is_file():return self.send(404,{'error':'文件不存在'})
                content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
                return self.send(200,target.read_bytes(),content_type,
                                 cache='public, max-age=31536000, immutable')
            return self.send(404,{'error':'页面不存在'})

        def _frontend(self, path):
            root=(ROOT/'frontend'/'dist').resolve()
            pages={'/','/jobs','/experiments','/comparisons','/models','/cases','/repositories','/guide'}
            normalized=path.rstrip('/') or '/'
            if normalized in pages:
                index=root/'index.html'
                if not index.is_file():return self.send(503,{'error':'前端页面尚未构建'})
                nonce=secrets.token_urlsafe(18)
                csp=(f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'nonce-{nonce}'; "
                     "style-src-attr 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
                     "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
                html=index.read_text(encoding='utf-8').replace('__STYLE_NONCE__',nonce).encode()
                return self.send(200,html,'text/html; charset=utf-8',csp=csp)
            if path.startswith('/assets/'):
                target=(root/path.lstrip('/')).resolve()
                try:target.relative_to(root)
                except ValueError:return self.send(404,{'error':'文件不存在'})
                if not target.is_file():return self.send(404,{'error':'文件不存在'})
                content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
                return self.send(200,target.read_bytes(),content_type,
                                 cache='public, max-age=31536000, immutable')
            return self.send(404,{'error':'页面不存在'})

        def _handle_get_error(self, exc):
            message = app.redact(str(exc)) if isinstance(exc,ValueError) else '文件不可用，请检查批次记录'
            return self.send(400, {'error':message})

        def do_POST(self):
            if not self.authorized(mutation=True):
                return
            try:
                length = int(self.headers.get('Content-Length','0'))
                if self.path.startswith('/api/uploads/'):
                    return self._resumable_upload(length)
                if self.path in {'/api/cases/upload', '/api/repositories/upload'}:
                    return self._upload_case_resource(length)
                if not 0 < length <= 16384:
                    raise ValueError('请求大小不合法')
                if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                    raise ValueError('请求必须为 JSON')
                request = json.loads(self.rfile.read(length))
                if not isinstance(request,dict):
                    raise ValueError('请求格式错误')
                return self._dispatch_post(request)
            except (ValueError, KeyError, OSError) as exc:
                return self._handle_post_error(exc)
            except Exception:
                self.send(500, {'error':'操作未完成，请查看日志'})

        def _resumable_upload(self, length):
            from . import resumable_uploads
            if os.environ.get('BENCH_CASE_UPLOAD') != '1':
                return self.send(404, {'error':'当前工作台未启用案例资源上传'})
            if self.path == '/api/uploads/start':
                if not 0 < length <= 16384 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('上传会话参数无效')
                value = json.loads(self.rfile.read(length))
                return self.send(201, resumable_uploads.create(app, value))
            match = re.fullmatch(r'/api/uploads/([a-f0-9]{32})/chunks/(\d+)', self.path)
            if match:
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/octet-stream':
                    raise ValueError('上传分块格式无效')
                return self.send(200, resumable_uploads.put_chunk(
                    app, match.group(1), int(match.group(2)), self.rfile, length))
            match = re.fullmatch(r'/api/uploads/([a-f0-9]{32})/(complete|abort)', self.path)
            if match:
                if length:
                    raise ValueError('请求大小不合法')
                if match.group(2) == 'complete':
                    return self.send(202, resumable_uploads.complete(app, match.group(1)))
                return self.send(200, resumable_uploads.abort(app, match.group(1)))
            return self.send(404, {'error':'上传操作不存在'})

        def _dispatch_post(self, request):
            if self.path == '/api/comparisons':
                from .experiment_view import submit_named
                return self.send(202, submit_named(app, request, comparison=True))
            if self.path in {'/api/cases/manage', '/api/repositories/manage'}:
                return self._manage_case_resource(request)
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
            return self.send(404, {'error':'操作不存在'})

        def _handle_post_error(self, exc):
            if isinstance(exc, OSError) and self.path in {'/api/cases/upload', '/api/repositories/upload'}:
                kind = '仓库 ZIP' if self.path == '/api/repositories/upload' else '案例 ZIP'
                print(f'{kind}导入文件错误：{type(exc).__name__}: {exc}', flush=True)
                message = kind+'导入时服务端文件读写失败，请检查服务终端日志、运行目录权限和磁盘空间'
            else:
                message = app.redact(str(exc)) if isinstance(exc,ValueError) else '操作参数或文件不可用'
            return self.send(400, {'error':message})

        def _upload_case_resource(self, length):
            if os.environ.get('BENCH_CASE_UPLOAD') != '1':
                return self.send(404, {'error':'当前工作台未启用案例上传'})
            from .case_upload import limits, receive_archive
            config = limits()
            if not 0 < length <= config['upload_max_bytes']:
                self.close_connection = True
                return self.send(413, {'error':'上传 ZIP 上限为 ' + config['upload_max_label']})
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/zip':
                self.close_connection = True
                raise ValueError('请选择 ZIP 文件上传')
            from .case_assets import submit_repository_upload
            repository_upload = self.path == '/api/repositories/upload'
            prefix, filename = ('repo-upload-', 'repositories.zip') if repository_upload else ('case-upload-', 'cases.zip')
            with tempfile.TemporaryDirectory(prefix=prefix, dir=app.data) as folder:
                archive = Path(folder) / filename
                receive_archive(self.rfile, archive, length)
                try:
                    if not repository_upload:
                        from .case_import import submit
                        return self.send(202, submit(app, archive))
                    status, result = submit_repository_upload(app, archive)
                    return self.send(status, result)
                except (zipfile.BadZipFile, RuntimeError):
                    kind = '仓库 ZIP' if repository_upload else '案例 ZIP'
                    raise ValueError(kind + '无效，请检查文件格式') from None
                except subprocess.CalledProcessError:
                    raise ValueError('案例 Git 历史准备失败，请查看服务终端；可以重新上传') from None

        def _manage_case_resource(self, request):
            if os.environ.get('BENCH_CASE_UPLOAD') != '1':
                return self.send(404, {'error':'当前工作台未开启资源管理'})
            from .case_assets import manage_case_dataset, manage_repository
            operation = manage_case_dataset if self.path == '/api/cases/manage' else manage_repository
            return self.send(200, operation(app, request))

        def _profile_mutation(self, method):
            if not self.authorized(mutation=True):
                return
            try:
                prefix = '/api/profiles/'
                if not self.path.startswith(prefix):
                    return self.send(404, {'error':'操作不存在'})
                from urllib.parse import unquote
                profile_id = unquote(self.path[len(prefix):])
                if not profile_id or '/' in profile_id or '\\' in profile_id:
                    raise ValueError('模型配置标识无效')
                if method == 'PUT':
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 16384 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                        raise ValueError('请求大小或格式不合法')
                    from .profiles import update_profile
                    result = update_profile(app, profile_id, json.loads(self.rfile.read(length)))
                else:
                    from .profiles import delete_profile
                    result = delete_profile(app, profile_id)
                self.send(200, result)
            except (ValueError, KeyError, OSError) as exc:
                self.send(400, {'error':app.redact(str(exc)) if isinstance(exc,ValueError) else '操作参数或文件不可用'})
            except Exception:
                self.send(500, {'error':'操作未完成，请查看日志'})

        def do_PUT(self):
            self._profile_mutation('PUT')

        def do_DELETE(self):
            self._profile_mutation('DELETE')
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
