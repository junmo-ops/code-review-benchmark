"""Build a small ZIP supplement for a commit missing from an uploaded repository."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "backend/src"))
sys.path.insert(0, str(ROOT))
from runner.git_bundles import git
from tools.build_repository_upload import repository_name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    base = git(args.repo, 'rev-parse', '--verify', args.base + '^{commit}').decode().strip()
    head = git(args.repo, 'rev-parse', '--verify', args.head + '^{commit}').decode().strip()
    name = repository_name(args.repo)
    slug = name.replace('/', '__')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='repo-repair-') as folder:
        temporary = Path(folder) / 'source.git'
        temporary.mkdir()
        git(temporary, 'init', '--bare', '--quiet')
        objects = git(args.repo, 'rev-parse', '--git-path', 'objects').decode().strip()
        objects = (args.repo.resolve() / objects).resolve()
        # Git expects LF; Windows text-mode CRLF can become part of the object path.
        (temporary / 'objects/info/alternates').write_bytes((objects.as_posix() + '\n').encode('utf-8'))
        git(temporary, 'update-ref', 'refs/heads/bench-repair', head)
        bundle = Path(folder) / (slug + '.bundle')
        git(temporary, 'bundle', 'create', str(bundle), 'refs/heads/bench-repair', '^' + base)
        checksum = hashlib.sha256()
        with bundle.open('rb') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                checksum.update(block)
        member = 'bundles/' + slug + '.bundle'
        manifest = {'format': 'code-review-benchmark-repositories-v1', 'repair': True,
                    'repositories': {slug: {'name': name, 'required_commits': [base, head]}},
                    'files': {member: {'size': bundle.stat().st_size, 'sha256': checksum.hexdigest()}}}
        output = args.output.with_name(args.output.name + '.part')
        try:
            with zipfile.ZipFile(output, 'w') as archive:
                archive.write(bundle, member)
                archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
            output.replace(args.output)
        finally:
            output.unlink(missing_ok=True)
    print('仓库补充包：' + str(args.output.resolve()))
    print('在新版工作台的仓库管理页面上传此 ZIP；无需重新上传完整仓库。')


if __name__ == '__main__':
    main()
