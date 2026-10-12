"""Build a repeatable Web UI case ZIP from a supported dataset and Git bundles."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "backend/src"))
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', required=True, choices=('aacr', 'swe_prbench_llm', 'codefuse_cr'))
    parser.add_argument('--name', required=True, help='页面中显示的案例集名称')
    parser.add_argument('--dataset', required=True, type=Path, help='符合对应案例集格式的 JSONL 文件')
    parser.add_argument('--bundles-dir', type=Path, default=ROOT / 'bundles')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    from runner.cloud import checked_instances

    dataset = args.dataset.resolve()
    cases = checked_instances(dataset, args.suite)
    if not args.name.strip() or len(args.name) > 100:
        parser.error('--name 必须为 1–100 个字符')
    files = {'dataset.jsonl': dataset}
    repo_slugs = {case.repo.replace('/', '__'): case.repo for case in cases}
    if len(repo_slugs) != len({case.repo for case in cases}):
        parser.error('仓库名替换为 bundle 文件名后发生冲突')
    slugs = sorted(repo_slugs)
    for slug in slugs:
        bundle = args.bundles_dir / (slug + '.bundle')
        if bundle.is_symlink() or not bundle.is_file():
            parser.error(f'缺少 Git bundle：{bundle}')
        files[f'bundles/{slug}.bundle'] = bundle
    records = {}
    for name, path in files.items():
        with path.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        records[name] = {'size': path.stat().st_size, 'sha256': digest}
    manifest = {
        'format': 'code-review-benchmark-cases-v1',
        'name': args.name.strip(),
        'suite': args.suite,
        'files': records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        for name, path in files.items():
            archive.write(path, name)
    size = args.output.stat().st_size
    print(f'案例集：{args.name.strip()}（{args.suite}，{len(cases)} 例）')
    print(f'ZIP：{args.output}（{size / 1024 / 1024:.1f} MiB）')
    from webui.case_upload import limits
    config = limits()
    if size > config['upload_max_bytes']:
        print('提示：ZIP 超过当前上传上限 ' + config['upload_max_label'] + '，上传前请调整 BENCH_CASE_UPLOAD_MAX_MIB。')


if __name__ == '__main__':
    main()
