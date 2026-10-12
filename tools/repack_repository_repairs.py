"""Rename supplement refs in an existing repair ZIP for legacy server compatibility."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile


def rewrite_bundle(source, target):
    first = source.readline()
    if first not in (b'# v2 git bundle\n', b'# v3 git bundle\n'):
        raise ValueError('不支持的 Git bundle 格式')
    target.write(first)
    while True:
        line = source.readline()
        if not line:
            raise ValueError('Git bundle 头部不完整')
        if line == b'\n':
            target.write(line)
            break
        match = re.fullmatch(rb'([a-f0-9]{40}|[a-f0-9]{64}) (refs/[^\n]+|HEAD)\n', line)
        if match:
            # Hash the original name so aliases of one commit still have distinct refs.
            name = b'refs/heads/bench-supplement-' + hashlib.sha256(match[2]).hexdigest().encode()
            line = match[1] + b' ' + name + b'\n'
        target.write(line)
    # The packed objects and their checksum are unchanged by renaming header refs.
    shutil.copyfileobj(source, target, length=1024 * 1024)


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def repack(input_path, output_path):
    if input_path.resolve() == output_path.resolve():
        raise ValueError('输出路径必须与原增量 ZIP 不同')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_name(output_path.name + '.part')
    try:
        with zipfile.ZipFile(input_path) as source, zipfile.ZipFile(partial, 'w') as output:
            manifest = json.loads(source.read('manifest.json'))
            if manifest.get('repair') is not True:
                raise ValueError('请使用增量补充 ZIP，不是完整仓库 ZIP 或案例配置 ZIP')
            with tempfile.TemporaryDirectory(prefix='repack-repair-') as folder:
                for member, record in manifest['files'].items():
                    rewrite_member(source, output, member, record, Path(folder))
            output.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
        partial.replace(output_path)
    finally:
        partial.unlink(missing_ok=True)


def rewrite_member(source, output, member, record, folder):
    if not re.fullmatch(r'bundles/[A-Za-z0-9_.-]+\.bundle', member):
        raise ValueError('增量 ZIP 包含不支持的文件：' + member)
    target = folder / 'rewritten.bundle'
    original = folder / 'original.bundle'
    with source.open(member) as reader, original.open('wb') as writer:
        shutil.copyfileobj(reader, writer, length=1024 * 1024)
    if original.stat().st_size != record['size'] or checksum(original) != record['sha256']:
        raise ValueError('原增量 ZIP 完整性校验失败：' + member)
    with original.open('rb') as reader, target.open('wb') as writer:
        rewrite_bundle(reader, writer)
    record.update(size=target.stat().st_size, sha256=checksum(target))
    output.write(target, member, compress_type=zipfile.ZIP_STORED)
    print('已转换：' + member, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repack(args.input, args.output)
    print('兼容版增量 ZIP：' + str(args.output.resolve()))
    print('在现有服务的仓库管理页上传此 ZIP，无需重新部署。')


if __name__ == '__main__':
    main()
