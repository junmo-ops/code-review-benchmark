"""Loopback-only protocol simulator: no repository access, LLM calls or real findings."""
import argparse
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def make_server(port=8877):
    tasks, keys = {}, {}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, value):
            body = json.dumps(value).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != '/review-tasks':
                return self.reply(404, {'error': 'unknown route'})
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size < 16384:
                    raise ValueError()
                data = json.loads(self.rfile.read(size))
                if set(data) != {'repository', 'base_commit', 'head_commit', 'diff_mode', 'model'}:
                    raise ValueError()
                key = self.headers['Idempotency-Key']
                if not key:
                    raise ValueError()
            except (ValueError, TypeError):
                return self.reply(400, {'error': 'invalid request'})
            with lock:
                if key in keys:
                    task_id = keys[key]
                    if tasks[task_id]['input'] != data:
                        return self.reply(409, {'error': 'idempotency conflict'})
                else:
                    task_id = str(uuid.uuid4())
                    keys[key] = task_id
                    tasks[task_id] = {'input': data, 'polls': 0}
            self.reply(202, {'task_id': task_id})

        def do_GET(self):
            parts = self.path.strip('/').split('/')
            if len(parts) not in (2, 3) or parts[0] != 'review-tasks' or parts[1] not in tasks:
                return self.reply(404, {'error': 'unknown task'})
            task_id = parts[1]
            with lock:
                task = tasks[task_id]
                if len(parts) == 2:
                    task['polls'] += 1
                    return self.reply(200, {'task_id': task_id, 'status': 'running' if task['polls'] == 1 else 'succeeded'})
                if parts[2] != 'result':
                    return self.reply(404, {'error': 'unknown route'})
                data = task['input']
                self.reply(200, dict(task_id=task_id, status='succeeded', complete=True, simulation=True,
                    scope={k: data[k] for k in ('repository', 'base_commit', 'head_commit', 'diff_mode')},
                    model=data['model'], engine_version='protocol-simulator/1', failed_files=0, comments=[], usage=None))

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.tasks = tasks
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8877)
    server = make_server(parser.parse_args().port)
    print(f'模拟服务 http://127.0.0.1:{server.server_port}；仅验证协议，不执行检视、不调用模型。', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
