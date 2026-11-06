"""Bounded Skill ZIP inspection. Package bytes are never executed or rewritten."""
import hashlib
import json
import posixpath
from pathlib import PurePosixPath
import re
import stat
import zipfile
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
import yaml

from .case_upload import _positive_setting, size_label


def limits():
    maximum = _positive_setting('BENCH_SKILL_UPLOAD_MAX_MIB', 32)
    return dict(upload_max_bytes=maximum, upload_max_label=size_label(maximum),
                unpacked_max_bytes=_positive_setting('BENCH_SKILL_UNPACK_MAX_MIB', 64),
                max_entries=2000, main_max_bytes=1024 * 1024)


def content_digest(files):
    encoded = json.dumps(files, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(encoded).hexdigest()


def _entry_path(info):
    name = info.orig_filename
    path = PurePosixPath(name)
    if (not name or '\\' in name or '\x00' in name or path.is_absolute()
            or any(part in {'', '.', '..'} for part in name.rstrip('/').split('/'))
            or re.match(r'^[A-Za-z]:', name)):
        raise ValueError('Skill ZIP 含不安全路径')
    if any(ord(char) < 32 for char in name):
        raise ValueError('Skill ZIP 路径含控制字符')
    mode = stat.S_IFMT(info.external_attr >> 16)
    allowed = {0, stat.S_IFDIR} if info.is_dir() else {0, stat.S_IFREG}
    if mode not in allowed or info.flag_bits & 1:
        raise ValueError('Skill ZIP 含链接、加密条目或不支持的文件类型')
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise ValueError('Skill ZIP 压缩类型不支持')
    return path.as_posix()


def _entries(source):
    infos = source.infolist()
    config = limits()
    if not infos or len(infos) > config['max_entries']:
        raise ValueError('Skill ZIP 条目数上限为 2000')
    seen, files = set(), {}
    for info in infos:
        name = _entry_path(info)
        if name.casefold() in seen:
            raise ValueError('Skill ZIP 含重复或大小写冲突条目')
        seen.add(name.casefold())
        if not info.is_dir():
            files[name] = info
    _validate_tree(files, seen)
    if sum(info.file_size for info in infos) > config['unpacked_max_bytes']:
        raise ValueError('Skill ZIP 解压大小超过上限')
    return files


def _validate_tree(files, seen):
    folded_files = {name.casefold() for name in files}
    for name in seen:
        if any(parent.as_posix() in folded_files for parent in PurePosixPath(name).parents
               if parent.as_posix() != '.'):
            raise ValueError('Skill ZIP 含文件与目录冲突')


def _metadata(main):
    if len(main) > limits()['main_max_bytes']:
        raise ValueError('SKILL.md 超过 1 MiB')
    text = main.decode('utf-8-sig')
    lines = text.splitlines()
    if not lines or lines[0] != '---':
        raise ValueError('SKILL.md 缺少 YAML 元数据')
    try:
        end = lines.index('---', 1)
        header = '\n'.join(lines[1:end])
        node = yaml.compose(header, Loader=yaml.SafeLoader)
        if isinstance(node, yaml.MappingNode):
            keys = [key.value for key, _ in node.value]
            if len(keys) != len(set(keys)):
                raise ValueError('重复元数据')
        metadata = yaml.safe_load(header)
    except (ValueError, yaml.YAMLError):
        raise ValueError('SKILL.md 元数据格式无效') from None
    if not isinstance(metadata, dict):
        raise ValueError('Skill 元数据必须为对象')
    for key, maximum in [('name', 100), ('description', 4096)]:
        value = metadata.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError('Skill 元数据缺少有效的 ' + key)
    return {key: metadata[key] for key in ('name', 'description')}


def unpack(archive, destination, progress):
    if archive.stat().st_size > limits()['upload_max_bytes']:
        raise ValueError('Skill ZIP 上传大小超过上限')
    try:
        with zipfile.ZipFile(archive) as source:
            entries = _entries(source)
            mains = [name for name in entries if PurePosixPath(name).name == 'SKILL.md']
            if len(mains) != 1:
                raise ValueError('一个包必须包含唯一的 SKILL.md')
            prefix = PurePosixPath(mains[0]).parent
            if any(not PurePosixPath(name).is_relative_to(prefix) for name in entries):
                raise ValueError('Skill 包含主说明目录之外的文件')
            return _extract(source, entries, mains[0], destination, progress)
    except (zipfile.BadZipFile, RuntimeError, UnicodeError, NotImplementedError) as error:
        raise ValueError('Skill ZIP 或主说明无效：' + str(error)) from None


def _extract(source, entries, main, destination, progress):
    files, total = [], 0
    metadata = None
    for index, (name, info) in enumerate(sorted(entries.items())):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with source.open(info) as stream, target.open('xb') as output:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > limits()['unpacked_max_bytes']:
                    raise ValueError('Skill ZIP 实际解压大小超过上限')
                digest.update(chunk)
                output.write(chunk)
        files.append(dict(path=name, size=target.stat().st_size, sha256=digest.hexdigest()))
        if name == main:
            metadata = _metadata(target.read_bytes())
        progress('extract', '正在校验 Skill 文件', index + 1, len(entries))
    _validate_references(destination, main, entries)
    return dict(metadata, main=main, files=files, content_sha256=content_digest(files))


def _links(tokens):
    for token in tokens:
        if token.type == 'link_open':
            yield token.attrGet('href')
        elif token.type == 'image':
            yield token.attrGet('src')
        yield from _links(token.children or [])


def verify_metadata(content, record):
    mains = [item['path'] for item in record['files'] if PurePosixPath(item['path']).name == 'SKILL.md']
    if mains != [record['main']]:
        raise ValueError('Skill 主说明引用不一致')
    metadata = _metadata((content / record['main']).read_bytes())
    if any(record[key] != value for key, value in metadata.items()):
        raise ValueError('Skill 元数据与主说明不一致')


def _validate_references(destination, main, entries):
    # Inspect CommonMark tokens only; raw HTML is never rendered or executed here.
    parser = MarkdownIt('commonmark')
    root = PurePosixPath(main).parent.as_posix()
    for name in entries:
        if not name.lower().endswith('.md'):
            continue
        text = (destination / name).read_text(encoding='utf-8-sig')
        for link in _links(parser.parse(text)):
            url = urlsplit(link)
            if url.scheme or url.netloc or not url.path:
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(url.path)))
            inside = PurePosixPath(target).is_relative_to(PurePosixPath(root))
            if not inside or target not in entries:
                raise ValueError('Skill 缺少或越界的本地引用资源：' + link)
