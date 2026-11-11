"""Move a stopped local workbench's data into runtime/ without merging files."""
import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/src'))
from runner.locking import acquire, release
from runner.runtime import LOCAL_DATA, legacy_profiles


def moves_for(root):
    runtime = root / 'runtime'
    pairs = [(root / name, runtime / name) for name in LOCAL_DATA]
    pairs.extend((path, runtime / 'models' / path.name) for path in legacy_profiles(root))
    return [(source, target) for source, target in pairs if source.exists() or source.is_symlink()]


def validate_moves(pairs):
    for source, target in pairs:
        if source.is_symlink() or target.exists() or target.is_symlink():
            raise ValueError(f'源是符号链接或目标已存在：{source.name} → {target}，请先处理冲突')
        if target.parent.is_symlink() or target.parent.parent.is_symlink():
            raise ValueError('运行目录不能使用符号链接')
    repos = next((source for source, _ in pairs if source.name == 'repos'), None)
    if repos is not None:
        for git_dir in repos.glob('*/.git'):
            linked = git_dir.is_file() or (git_dir / 'objects/info/alternates').exists()
            if linked or any((git_dir / 'worktrees').glob('*')):
                raise ValueError(f'仓库存在外部对象或关联工作树，请先处理：{git_dir.parent.name}')


def ensure_idle(root):
    # Release handles before renaming directories, which Windows does not allow with open files.
    locks = [root / 'ui-state' / name for name in ('server.lock', 'worker.lock')]
    locks.extend((root / 'runs').rglob('.cloud.lock'))
    locks.extend((root / 'ui-state').glob('**/worker.lock'))
    with ExitStack() as stack:
        for path in sorted(set(locks)):
            if path.is_file():
                handle = stack.enter_context(path.open('r+b'))
                acquire(handle)
                stack.callback(release, handle)


def apply_moves(pairs):
    done = []
    try:
        for source, target in pairs:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            done.append((source, target))
    except OSError:
        for source, target in reversed(done):
            target.rename(source)
        raise


def main():
    parser = argparse.ArgumentParser(description='收拢本地运行数据。默认只预览，停止工作台及 CLI 后用 --apply 执行。')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if {'BENCH_DATA_ROOT', 'BENCH_CONFIG_ROOT'} & os.environ.keys():
        parser.error('当前配置了自定义运行目录，请在使用默认目录的终端执行此命令')
    try:
        pairs = moves_for(ROOT)
        validate_moves(pairs)
        for source, target in pairs:
            print(f'{source.relative_to(ROOT)} → {target.relative_to(ROOT)}')
        if not pairs:
            print('没有旧目录需要迁移。')
        elif args.apply:
            ensure_idle(ROOT)
            apply_moves(pairs)
            print('运行数据已迁移。')
        else:
            print('预览完成。停止工作台、Worker 和测评命令后加 --apply 执行。')
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
