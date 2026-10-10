"""Build a bundle-free AACR 50-case configuration ZIP from local selection results."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def select_cases(dataset_bytes, result):
    selected = validate_selection(dataset_bytes, result)
    all_cases = [json.loads(line) for line in dataset_bytes.decode('utf-8-sig').splitlines() if line.strip()]
    indexed = {case['instance_id']: case for case in all_cases}
    availability = {case.get('instance_id'): case.get('status') for case in result.get('cases', [])}
    if len(indexed) != len(all_cases) or any(case_id not in indexed or availability.get(case_id) != '可用' for case_id in selected):
        raise ValueError('已选案例与本地 Git 历史检查结果不匹配')
    return [indexed[case_id] for case_id in selected]


def validate_selection(dataset_bytes, result):
    if hashlib.sha256(dataset_bytes).hexdigest() != result.get('dataset_sha256'):
        raise ValueError('检查结果未确认从当前 AACR 清单凑足 50 例')
    if result.get('requested') != 50 or result.get('selected') != 50 or not result.get('enough'):
        raise ValueError('检查结果未确认从当前 AACR 清单凑足 50 例')
    selected = result.get('selected_instance_ids')
    if not isinstance(selected, list) or len(selected) != 50 or len(set(selected)) != 50:
        raise ValueError('检查结果中的 50 个案例编号无效或重复')
    return selected


def write_case_config(output, cases):
    dataset = ('\n'.join(json.dumps(case, ensure_ascii=False) for case in cases) + '\n').encode('utf-8')
    slugs = sorted({case['repo'].replace('/', '__') for case in cases})
    manifest = {'format': 'code-review-benchmark-case-config-v1',
                'name': 'AACR-Bench 官方正样本 50 例', 'suite': 'aacr',
                'repositories': slugs,
                'files': {'dataset.jsonl': {'size': len(dataset),
                                            'sha256': hashlib.sha256(dataset).hexdigest()}}}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.part')
    try:
        with zipfile.ZipFile(temporary, 'w', allowZip64=True, compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('dataset.jsonl', dataset)
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    print('案例配置包完成：50 例，' + str(len(slugs)) + ' 个仓库；未包含 Git bundle。')
    print('ZIP：' + str(output.resolve()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-result', required=True, type=Path, help='aacr-selection-result/check-result.json')
    parser.add_argument('--dataset', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'data/sources/aacr/positive_samples.standard.jsonl')
    parser.add_argument('--output', type=Path, default=Path('aacr-bench-50-cases.zip'))
    args = parser.parse_args()
    if not args.dataset.is_file() or not args.check_result.is_file():
        parser.error('找不到 AACR 正样本清单或 check-result.json')
    dataset_bytes = args.dataset.read_bytes()
    result = json.loads(args.check_result.read_text(encoding='utf-8-sig'))
    try:
        cases = select_cases(dataset_bytes, result)
    except ValueError as error:
        parser.error(str(error))
    write_case_config(args.output, cases)


if __name__ == '__main__':
    main()
