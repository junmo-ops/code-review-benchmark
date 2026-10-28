"""Loopback-only deterministic model/judge fixture; never a DSH replacement."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading


def completion(body, index):
    model = body['model']
    if model == 'dsh-judge':
        text = json.dumps(dict(match=True, confidence=1, reasoning='local deterministic match'))
        if len(body['messages']) == 1:
            text = 'yes'
        return dict(content=text), 'stop'
    if model == 'dsh-empty':
        return dict(content=json.dumps(dict(status='complete', comments=[]))), 'stop'
    if not any(m.get('role') == 'tool' for m in body['messages']):
        return dict(content=None, tool_calls=[dict(id='read-head', type='function',
            function=dict(name='read', arguments=json.dumps(dict(file_path='sample.py'))))]), 'tool_calls'
    text = json.dumps(dict(status='complete', comments=[dict(path='sample.py', start_line=1,
        end_line=1, content='本地确定性缺陷意见，仅验证测评流程。')]), ensure_ascii=False)
    return dict(content=text), 'stop'


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            self.respond()
        except (BrokenPipeError, ConnectionResetError):
            pass  # A timeout probe deliberately closes the model connection.

    def respond(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(payload)
        message, reason = self.server.respond(payload, len(self.server.requests))
        if message.get('http_error'):
            data = json.dumps(message['http_error']).encode()
            self.send_response(message.get('http_status', 503))
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        result = dict(id='local-only', object='chat.completion', created=1, model=payload['model'],
            choices=[dict(index=0, message=dict(role='assistant', **message), finish_reason=reason)],
            usage=self.server.usage)
        self.send_response(200)
        if payload.get('stream'):
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            result.update(object='chat.completion.chunk', choices=[dict(index=0,
                delta=dict(role='assistant', **message), finish_reason=reason)])
            self.wfile.write(('data: ' + json.dumps(result, ensure_ascii=False) + '\n\ndata: [DONE]\n\n').encode())
        else:
            data = json.dumps(result, ensure_ascii=False).encode()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def log_message(self, *args):
        # Tests inspect server.requests; keep routine fixture HTTP logs off stderr.
        pass


@contextmanager
def serve(respond=completion):
    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        server.requests = []
        server.respond = respond
        server.usage = dict(prompt_tokens=100, completion_tokens=10, total_tokens=110,
                           prompt_tokens_details=dict(cached_tokens=90))
        server.url = f'http://127.0.0.1:{server.server_port}/v1'
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=2)
