"""Prepare the Windows local AACR runtime from pre-staged offline assets."""
from pathlib import Path
import argparse, json, os, platform, shutil, subprocess, sys

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "backend/src"))

def validate_platform(parser):
    if platform.system() != 'Windows' or platform.machine().lower() not in ('amd64', 'x86_64'):
        parser.error('Windows 本地运行包要求 Windows x64')
    if not shutil.which('node') or not shutil.which('npm') or not shutil.which('git'):
        parser.error('PATH 中需要 Node.js、npm 和 Git')

def validate_package(parser, binary, package_root, runtime_spec):
    if not binary.is_file():
        parser.error(f"公司 npm 源的 {runtime_spec['npm_package']}@{runtime_spec['npm_version']} 未包含 Windows 可执行文件 {runtime_spec['binary']}")
    package_manifest = json.loads((package_root/'package.json').read_text(encoding='utf-8'))
    if package_manifest.get('version') != runtime_spec['npm_version']:
        parser.error('安装到的 LCR npm 包版本与本地固定版本不一致')

def ensure_case_bundles(items, parser):
    missing = [name for name in sorted({item.repo for item in items})
               if not (ROOT/'bundles'/(name.replace('/', '__')+'.bundle')).is_file()
               and not (ROOT/'bundles'/(name.replace('/', '__')+'.git.tar.gz')).is_file()]
    if missing: parser.error('缺少 AACR 案例仓库历史：'+', '.join(missing))

def restore_repositories(parser):
    sys.path.insert(0, str(ROOT))
    from runner.cloud import checked_instances
    from runner.runtime import data_root
    from tools.repo_snapshot import restore_snapshot
    repos_root = data_root(ROOT)/'repos'
    items = checked_instances(ROOT/'data'/'pilot5.jsonl', 'aacr')
    ensure_case_bundles(items, parser)
    for name in sorted({item.repo for item in items}):
        repo = repos_root/name.replace('/', '__')
        bundle = ROOT/'bundles'/(name.replace('/', '__')+'.bundle')
        if not repo.exists():
            repo.parent.mkdir(parents=True, exist_ok=True)
            if bundle.exists(): subprocess.run(['git', 'clone', '--no-checkout', str(bundle), str(repo)], check=True)
            else: restore_snapshot(ROOT/'bundles'/(name.replace('/', '__')+'.git.tar.gz'), repo)
            head = next(item.head_commit for item in items if item.repo == name)
            env = dict(os.environ, GIT_LFS_SKIP_SMUDGE='1')
            subprocess.run(['git','-C',str(repo),'-c','filter.lfs.process=','-c','filter.lfs.smudge=','-c','filter.lfs.required=false','checkout','--detach',head],env=env,check=True)
        for item in (item for item in items if item.repo == name):
            for expression in (item.base_commit+'^{commit}', item.head_commit+'^{commit}'):
                subprocess.run(['git','-C',str(repo),'cat-file','-e',expression],check=True)
            subprocess.run(['git','-C',str(repo),'merge-base',item.base_commit,item.head_commit],check=True,capture_output=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore-only', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    validate_platform(parser)
    runtime_spec = json.loads((ROOT/'backend/src/reviewers/lcr/windows-runtime.json').read_text(encoding='utf-8'))
    package_root = ROOT/'.runtime/lcr/node_modules'/runtime_spec['npm_package'].replace('/', os.sep)
    binary = package_root/'dist'/runtime_spec['binary']
    venv = ROOT/'.venv'
    if not venv.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    py = venv/'Scripts'/'python.exe'
    if not args.restore_only:
        # Share supported constraints with the API and container; skip Gunicorn on Windows.
        pip_args = [str(py), '-m', 'pip', 'install', '--disable-pip-version-check',
                    '-r', str(ROOT/'backend/requirements.txt')]
        subprocess.run(pip_args, check=True)
        npm_args = [shutil.which('npm'), 'install', '--prefix', str(ROOT/'.runtime/lcr'), '--no-audit', '--no-fund', f"{runtime_spec['npm_package']}@{runtime_spec['npm_version']}"]
        subprocess.run(npm_args, cwd=ROOT, check=True)
        subprocess.run([str(py), str(Path(__file__).resolve()), '--restore-only'], cwd=ROOT, check=True)
        return
    validate_package(parser, binary, package_root, runtime_spec)
    launcher = [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', r'call bin\lcr.cmd version']
    subprocess.run(launcher, cwd=ROOT, check=True)
    restore_repositories(parser)
    (ROOT/'local-runtime.json').write_text(json.dumps({
        'platform': 'Windows', 'architecture': platform.machine(),
        'python': '.'.join(map(str, sys.version_info[:3])),
        'binary': binary.name, 'suite': 'aacr', 'assets': 'company_package_sources',
        'npm_package': runtime_spec['npm_package'], 'npm_version': runtime_spec['npm_version']
    }, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print('Windows 本地 AACR 运行环境已准备；未调用模型。')

if __name__ == '__main__':
    main()
