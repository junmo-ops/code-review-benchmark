"""Shared upload limits and bounded streaming for local and server workbenches."""
import os

MIB = 1024 * 1024


def _positive_setting(name, default):
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        raise ValueError(f'{name} 必须为正整数（MiB）') from None
    if value <= 0:
        raise ValueError(f'{name} 必须为正整数（MiB）')
    return value * MIB


def size_label(size):
    if size >= 1024 * MIB:
        return f'{size / (1024 * MIB):g} GiB'
    return f'{size / MIB:g} MiB'


def limits():
    maximum = _positive_setting('BENCH_CASE_UPLOAD_MAX_MIB', 2048)
    unpacked = _positive_setting('BENCH_CASE_UNPACK_MAX_MIB', 8192)
    return dict(upload_max_bytes=maximum, upload_max_label=size_label(maximum),
                unpacked_max_bytes=unpacked, unpacked_max_label=size_label(unpacked))


def receive_archive(stream, destination, length=None):
    maximum = limits()['upload_max_bytes']
    if length is not None and not 0 < length <= maximum:
        raise ValueError('案例 ZIP 上限为 ' + size_label(maximum))
    size = 0
    with destination.open('wb') as output:
        while length is None or size < length:
            request_size = min(MIB, maximum - size + 1)
            if length is not None:
                request_size = min(request_size, length - size)
            chunk = stream.read(request_size)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise ValueError('案例 ZIP 上限为 ' + size_label(maximum))
            output.write(chunk)
    if not size or (length is not None and size != length):
        raise ValueError('上传文件为空或传输未完成，请重新上传')
