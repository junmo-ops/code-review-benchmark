"""Build a lightweight case ZIP that references repositories in the workbench pool."""
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
    parser.add_argument('--name', required=True)
    parser.add_argument('--dataset', required=True, type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.name.strip() or len(args.name) > 100:
        parser.error('--name 必须为 1–100 个字符')
    from runner.cloud import checked_instances
    cases = checked_instances(args.dataset, args.suite)
    slugs = sorted({case.repo.replace('/', '__') for case in cases})
    raw = args.dataset.read_bytes()
    records = {'dataset.jsonl': {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}}
    manifest = {'format': 'code-review-benchmark-case-config-v1', 'name': args.name,
                'suite': args.suite, 'repositories': slugs, 'files': records}
    temporary = args.output.with_name(args.output.name + '.part')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(temporary, 'w', allowZip64=True, compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(args.dataset, 'dataset.jsonl')
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(args.output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    print(f'案例配置包：{args.output.resolve()}；案例 {len(cases)} 例；仓库 {len(slugs)} 个')


if __name__ == '__main__':
    main()
