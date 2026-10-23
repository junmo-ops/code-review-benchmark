"""Loopback S3 wire substitute for real boto3 requests; never retains credentials."""
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit, unquote, parse_qs
from xml.sax.saxutils import escape


class S3HTTP:
    def __init__(self, mode='atomic'):
        self.mode = mode
        self.lock = threading.Lock()
        self.objects = {}
        self.requests = []
        self.record_heads = None
        self.head_count = 0
        self.race = threading.Barrier(2)

    def record(self, method, path, headers, status):
        # Intentionally omit Authorization, credential IDs, tokens and signatures.
        allowed = ('if-none-match', 'content-length', 'x-amz-meta-sha256', 'x-cos-forbid-overwrite')
        row = dict(method=method, path=path, status=status,
                   headers={name: headers[name] for name in allowed if name in headers})
        signature = re.search(r'SignedHeaders=([^,\s]+)', headers.get('Authorization', ''))
        row['signed_headers'] = signature[1].split(';') if signature else []
        with self.lock:
            self.requests.append(row)


def handler_for(store):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def respond(self, status, body=b'', headers=None):
            store.record(self.command, self.path, self.headers, status)
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def error(self, status, code):
            self.respond(status, f'<Error><Code>{code}</Code></Error>'.encode())

        def key(self):
            return unquote(urlsplit(self.path).path)

        def do_PUT(self):
            key = self.key()
            raw = self.rfile.read(int(self.headers['Content-Length']))
            if store.mode == 'reject' and self.headers.get('If-None-Match'):
                return self.error(400, 'NotImplemented')
            if store.mode == 'racy' and key.endswith('/race'):
                with store.lock:
                    duplicate = key in store.objects
                store.race.wait(timeout=10)
                with store.lock:
                    if not duplicate:
                        store.objects[key] = (raw, self.headers.get('x-amz-meta-sha256', ''))
                return self.respond(200) if not duplicate else self.error(412, 'PreconditionFailed')
            with store.lock:
                duplicate = key in store.objects
                forbidden = store.mode in {'atomic', 'racy', 'accepted'} and self.headers.get('If-None-Match') == '*'
                if not (duplicate and forbidden):
                    store.objects[key] = (raw, self.headers.get('x-amz-meta-sha256', ''))
            if duplicate and forbidden:
                return self.error(412, 'PreconditionFailed')
            status = 202 if store.mode == 'accepted' else 200
            self.respond(status, headers={'ETag': '"' + hashlib.md5(raw).hexdigest() + '"'})

        def do_HEAD(self):
            key = self.key()
            with store.lock:
                value = store.objects.get(key)
                barrier = store.record_heads if key.endswith('/record.json') and store.head_count < 2 else None
                if barrier:
                    store.head_count += 1
            if barrier:
                barrier.wait(timeout=10)
            if value is None:
                return self.error(404, 'NoSuchKey')
            raw, digest = value
            self.respond(200, b'x' * len(raw), {'x-amz-meta-sha256': digest})

        def do_GET(self):
            query = urlsplit(self.path).query
            if query.startswith('list-type='):
                prefix = parse_qs(query).get('prefix', [''])[0]
                with store.lock:
                    contents = ''.join('<Contents><Key>' + escape(key.split('/', 2)[2]) + '</Key><Size>'
                                       + str(len(value[0])) + '</Size></Contents>'
                                       for key, value in store.objects.items()
                                       if key.split('/', 2)[2].startswith(prefix))
                return self.respond(200, ('<ListBucketResult><IsTruncated>false</IsTruncated>'
                                         + contents + '</ListBucketResult>').encode())
            with store.lock:
                value = store.objects.get(self.key())
            if value is None:
                return self.error(404, 'NoSuchKey')
            raw, digest = value
            self.respond(200, raw, {'x-amz-meta-sha256': digest})

        def do_DELETE(self):
            with store.lock:
                store.objects.pop(self.key(), None)
            self.respond(204)

    return Handler


@contextmanager
def serve_s3(mode='atomic'):
    store = S3HTTP(mode)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(store))
    store.url = f'http://127.0.0.1:{server.server_port}'
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield store
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        destination = os.getenv('S3_HTTP_EVIDENCE_ROOT')
        if destination:
            folder = Path(destination)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / (str(time.time_ns()) + '.json')).write_text(json.dumps(dict(
                endpoint=store.url, mode=mode, requests=store.requests), ensure_ascii=False, indent=2))
