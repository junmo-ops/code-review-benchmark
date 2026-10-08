"""Verify and extract uploaded AACR case history; no model calls."""
import argparse
import hashlib
import json
from pathlib import Path
import stat
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def stage(archive, manifest, destination_root=None):
    destination_root = Path(destination_root) if destination_root is not None else ROOT
    expected = manifest['files']
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) != len(expected) or {e.filename for e in entries} != set(expected):
            raise ValueError('案例 ZIP 文件清单不匹配，请使用对应版本的案例包')
        for entry in entries:
            record = expected[entry.filename]
            path = Path(entry.filename)
            if path.is_absolute() or '..' in path.parts or path.parts[0] != 'bundles':
                raise ValueError('案例资源路径无效')
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if entry.is_dir() or mode not in (0, stat.S_IFREG) or entry.file_size != record['size']:
                raise ValueError('案例资源类型或大小不匹配')
            destination = destination_root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_symlink() or destination.parent.is_symlink():
                raise ValueError('案例资源目录不能为符号链接')
            if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != record['sha256']:
                raise ValueError('已有案例资源不同，停止覆盖：' + entry.filename)
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
                temporary = Path(output.name)
                digest = hashlib.sha256()
                try:
                    with source.open(entry) as content:
                        while chunk := content.read(1024 * 1024):
                            digest.update(chunk)
                            output.write(chunk)
                    output.close()
                    if digest.hexdigest() != record['sha256']:
                        raise ValueError('案例资源 SHA256 不匹配')
                    temporary.chmod(0o644)
                    if not destination.exists():
                        temporary.replace(destination)
                finally:
                    temporary.unlink(missing_ok=True)
    print('AACR 案例包已校验并准备到 bundles/；未调用模型。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True, help='案例 ZIP 的本地路径')
    args = parser.parse_args()
    manifest = json.loads((ROOT / 'deployment/aacr-cases-manifest.json').read_text(encoding='utf-8'))
    stage(args.archive, manifest)


if __name__ == '__main__':
    main()
