"""Short-lived, path-bound tickets for browser-streamed ZIP downloads."""
import re
from itsdangerous import URLSafeTimedSerializer, BadSignature
from flask import request


def serializer(secret):
    return URLSafeTimedSerializer(secret, salt='zip-tool-download-v1')


def valid(secret):
    if request.method != 'GET' or not re.fullmatch(r'/api/zip-tool/[a-f0-9]{32}/download', request.path):
        return False
    try:
        value = serializer(secret).loads(request.args.get('ticket', ''), max_age=120)
    except BadSignature:
        return False
    return value == [request.path, request.args.get('kind', 'zip')]
