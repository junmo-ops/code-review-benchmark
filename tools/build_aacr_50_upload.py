"""Build the standard AACR upload ZIP from selector output and local repositories."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


def validate_report(result, dataset_bytes):
    if hashlib.sha256(dataset_bytes).hexdigest() != result.get('dataset_sha256'):
        raise ValueError('检查结果与本 ZIP 内的 AACR 官方清单版本不匹配')
    if result.get('requested') != 50 or result.get('selected') != 50 or not result.get('enough'):
        raise ValueError('检查结果没有确认凑足 50 例')
    selected = result.get('selected_instance_ids')
    if not isinstance(selected, list) or len(selected) != 50 or len(set(selected)) != 50:
        raise ValueError('检查结果中的 50 个案例编号无效或重复')


def selected_cases(dataset_bytes, result):
    selected = result['selected_instance_ids']
    cases = [json.loads(line) for line in dataset_bytes.decode('utf-8-sig').splitlines() if line.strip()]
    by_id = {case['instance_id']: case for case in cases}
    if len(by_id) != len(cases) or any(case_id not in by_id for case_id in selected):
        raise ValueError('已选案例与官方数据清单不匹配')
    rows = {row.get('instance_id'): row for row in result.get('cases', [])}
    chosen = [by_id[case_id] for case_id in selected]
    if any(rows.get(case['instance_id'], {}).get('status') != '可用' for case in chosen):
        raise ValueError('所选案例没有全部通过本地 Git 完整性检查')
    return chosen, rows


def load_inputs(dataset_path, result_path):
    dataset_bytes = dataset_path.read_bytes()
    result = json.loads(result_path.read_text(encoding='utf-8-sig'))
    validate_report(result, dataset_bytes)
    cases, rows = selected_cases(dataset_bytes, result)
    return result, cases, rows


def hash_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def run_bundle(repo, commits, archive, slug, file_records):
    path = Path(repo).resolve()
    git_marker = path / '.git'
    if not path.is_dir() or not git_marker.exists():
        raise ValueError('本地仓库已不存在或 .git 已丢失：' + str(path))
    unique_commits = sorted(set(commits))
    args = ['git', '-c', 'safe.directory=' + str(path), '-c', 'core.fsmonitor=false',
            '-c', 'protocol.allow=never', '-C', str(path), 'bundle', 'create', '-', *unique_commits]
    print('正在打包仓库历史：' + slug + '（' + str(len(unique_commits)) + ' 个提交）', flush=True)
    digest = hashlib.sha256()
    size = 0
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors,
                                   env=dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_NO_LAZY_FETCH='1'))
        try:
            with archive.open('bundles/' + slug + '.bundle', 'w') as target:
                while True:
                    chunk = process.stdout.read(1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            code = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
    if code:
        errors.seek(0)
        detail = errors.read(4096).decode('utf-8', 'replace').strip()
        raise ValueError('Git bundle 创建失败：' + slug + (('；' + detail[-500:]) if detail else ''))
    file_records['bundles/' + slug + '.bundle'] = {'size': size, 'sha256': digest.hexdigest()}
    print('完成：' + slug + '（' + format(size / 1024 / 1024, '.1f') + ' MiB）', flush=True)


def make_bundles(cases, rows, archive, records):
    groups = {}
    for case in cases:
        slug = case['repo'].replace('/', '__')
        if not slug.replace('_', 'a').replace('-', 'a').replace('.', 'a').isalnum():
            raise ValueError('仓库名称不能安全转换为 bundle 文件名：' + case['repo'])
        if slug in groups and groups[slug]['repo'] != case['repo']:
            raise ValueError('仓库名称转换后发生冲突：' + slug)
        group = groups.setdefault(slug, {'repo': case['repo'], 'path': rows[case['instance_id']]['local_path'], 'commits': set()})
        if rows[case['instance_id']]['local_path'] != group['path']:
            raise ValueError('同一仓库对应了多个本地路径：' + case['repo'])
        group['commits'].update((case['base_commit'], case['head_commit']))
    for slug, group in sorted(groups.items()):
        run_bundle(group['path'], group['commits'], archive, slug, records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-result', required=True, type=Path, help='aacr-selection-result/check-result.json')
    parser.add_argument('--dataset', type=Path, default=Path(__file__).resolve().with_name('aacr-official.jsonl'))
    parser.add_argument('--output', type=Path, default=Path('aacr-bench-50-cases.zip'))
    args = parser.parse_args()
    if not args.dataset.is_file() or not args.check_result.is_file():
        parser.error('找不到已选案例清单或 check-result.json')
    result, cases, rows = load_inputs(args.dataset, args.check_result)
    slugs = {case['repo'].replace('/', '__') for case in cases}
    if len(slugs) != len({case['repo'] for case in cases}):
        parser.error('仓库名称转换后发生冲突')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + '.part')
    records = {}
    try:
        with zipfile.ZipFile(temporary, 'w', allowZip64=True) as archive:
            dataset_bytes = ('\n'.join(json.dumps(case, ensure_ascii=False) for case in cases) + '\n').encode('utf-8')
            archive.writestr('dataset.jsonl', dataset_bytes, compress_type=zipfile.ZIP_DEFLATED)
            records['dataset.jsonl'] = {'size': len(dataset_bytes), 'sha256': hashlib.sha256(dataset_bytes).hexdigest()}
            make_bundles(cases, rows, archive, records)
            manifest = {'format': 'code-review-benchmark-cases-v1',
                        'name': 'AACR-Bench 官方正样本 50 例', 'suite': 'aacr', 'files': records}
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        with zipfile.ZipFile(temporary) as archive:
            if 'manifest.json' not in archive.namelist():
                raise ValueError('生成的案例 ZIP 缺少清单文件')
        temporary.replace(args.output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    size = args.output.stat().st_size
    print('案例包完成：50 例，' + str(len(slugs)) + ' 个仓库')
    print('ZIP：' + str(args.output.resolve()) + '（' + format(size / 1024 / 1024, '.1f') + ' MiB）')
    print('SHA256：' + hash_file(args.output))


if __name__ == '__main__':
    main()
