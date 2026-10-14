"""Prepare read-only, full Git-history uploads for an existing AACR selection."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(Path(__file__).resolve().parent)]
from select_aacr_local import discover, index_repos, check_case
from fixtures import case_archive


def source_identity(path):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_NO_LAZY_FETCH='1')
    refs = subprocess.check_output(['git', '-C', str(path), 'show-ref', '--head'], env=env)
    return hashlib.sha256(refs).hexdigest()


def archive_repository(path, target):
    history = path / '.git'
    if not history.is_dir() or (history / 'objects/info/alternates').exists():
        raise ValueError('验收只接受自包含的 .git 目录：' + str(path))
    before = source_identity(path)
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as archive:
        for item in sorted(history.rglob('*')):
            if item.is_symlink():
                raise ValueError('Git 历史含链接，停止准备：' + str(item))
            if item.is_file():
                archive.write(item, 'outer/' + path.name + '/.git/' + item.relative_to(history).as_posix())
    if source_identity(path) != before:
        raise ValueError('准备期间源仓库引用发生变化：' + str(path))
    return dict(path=str(path), refs_sha256=before, archive=str(target), bytes=target.stat().st_size)


def selected_cases(selection, dataset):
    report = json.loads(selection.read_text(encoding='utf-8-sig'))
    if hashlib.sha256(dataset.read_bytes()).hexdigest() != report['dataset_sha256']:
        raise ValueError('原选择报告与当前 AACR 数据集不匹配')
    wanted = report['selected_instance_ids']
    indexed = {row['instance_id']: row for row in
               (json.loads(line) for line in dataset.read_text().splitlines() if line.strip())}
    if len(set(wanted)) != 50 or len(wanted) != 50:
        raise ValueError('需要原来的 50 个唯一案例')
    return [indexed[key] for key in wanted]


def prepare(args):
    cases = selected_cases(args.selection, args.dataset)
    warnings = []
    indexed = index_repos(discover([args.repos_root], warnings), {c['repo'].lower() for c in cases}, warnings)
    checked = [check_case(case, indexed.get(case['repo'].lower(), [])) for case in cases]
    missing = [case for case in checked if case['status'] != '可用']
    if warnings or missing:
        raise ValueError('仓库检查未通过：' + json.dumps(dict(warnings=warnings, missing=missing), ensure_ascii=False))
    args.output.mkdir(parents=True, exist_ok=False)
    paths = {row['repo']: Path(row['local_path']) for row in checked}
    repositories = []
    for name, path in sorted(paths.items()):
        target = args.output / (name.replace('/', '__') + '.zip')
        record = dict(name=name, **archive_repository(path, target))
        repositories.append(record)
        print(f'Prepared {name}: {record["bytes"] / 2**20:.1f} MiB', flush=True)
    case_archive(args.output / 'cases.zip', cases, 'AACR真实仓库50例验收')
    recovery = [case for case in cases if case['repo'] == 'CherryHQ/cherry-studio'][:2]
    if len(recovery) != 2:
        raise ValueError('原 50 例缺少两个 Cherry Studio 恢复验收案例')
    case_archive(args.output / 'recovery-cases.zip', recovery, 'AACR真实仓库恢复验收')
    manifest = dict(repositories=repositories, cases=cases, recovery=recovery,
                    case_archive=str(args.output / 'cases.zip'),
                    recovery_archive=str(args.output / 'recovery-cases.zip'),
                    dataset_sha256=hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
                    selection_sha256=hashlib.sha256(args.selection.read_bytes()).hexdigest(),
                    boundary='Full .git histories, original working files omitted; original repositories unchanged')
    (args.output / 'real-suite.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print('Real suite: ' + str(args.output / 'real-suite.json'), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repos-root', type=Path, required=True)
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, default=ROOT / 'data/sources/aacr/positive_samples.standard.jsonl')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    if not args.output.is_relative_to(ROOT / 'output/playwright'):
        parser.error('验收资源请保存在项目 output/playwright 隔离目录')
    prepare(args)


if __name__ == '__main__':
    main()
