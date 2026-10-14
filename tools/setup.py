"""Offline bootstrap; never calls an LLM."""
from pathlib import Path
import argparse, hashlib, json, os, platform, subprocess, sys
ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "backend/src"))
sys.path.insert(0,str(ROOT))
from runner.cloud import checked_instances
from tools.repo_snapshot import restore_snapshot
from runner.runtime import data_root

def command(*args, **kw):
    subprocess.run(list(map(str,args)),check=True,**kw)

def restore_repositories(cases, bundle_root=None, repos_root=None):
    bundle_root = Path(bundle_root) if bundle_root is not None else ROOT / 'bundles'
    repos_root = Path(repos_root) if repos_root is not None else data_root(ROOT) / 'repos'
    items=[dict(repo=i.repo,base_commit=i.base_commit,head_commit=i.head_commit) for i in cases]
    for name in sorted({item['repo'] for item in items}):
        slug=name.replace('/','__');repo=repos_root/slug;bundle=bundle_root/(slug+'.bundle')
        if not repo.exists():
            repo.parent.mkdir(parents=True,exist_ok=True)
            if bundle.exists():
                command('git','clone','--no-checkout',bundle,repo)
            else:
                snapshot=bundle_root/(slug+'.git.tar.gz')
                restore_snapshot(snapshot,repo)
            head=next(i['head_commit'] for i in items if i['repo']==name)
            env=dict(os.environ,GIT_LFS_SKIP_SMUDGE='1')
            command('git','-C',repo,'-c','filter.lfs.process=','-c','filter.lfs.smudge=','-c','filter.lfs.required=false','checkout','--detach',head,env=env)
        for item in (i for i in items if i['repo']==name):
            command('git','-C',repo,'cat-file','-e',item['base_commit']+'^{commit}')
            command('git','-C',repo,'cat-file','-e',item['head_commit']+'^{commit}')
            command('git','-C',repo,'merge-base',item['base_commit'],item['head_commit'])

def main():
    p=argparse.ArgumentParser();p.add_argument('--repos-only',action='store_true')
    p.add_argument('--suite',default='aacr')
    p.add_argument('--dataset',default='data/pilot5.jsonl')
    args=p.parse_args()
    dataset=Path(args.dataset)
    if not dataset.is_absolute():dataset=ROOT/dataset
    cases=checked_instances(dataset,args.suite)
    manifest=ROOT/'assets-manifest.json'
    if manifest.exists():
        for name,expected in json.loads(manifest.read_text(encoding='utf-8')).items():
            if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=expected:
                raise SystemExit('文件校验失败：'+name)
        print('交付文件校验通过',flush=True)
    if not args.repos_only:
        if platform.system()!='Linux' or platform.machine() not in ('x86_64','AMD64') or sys.version_info[:2]!=(3,11):
            raise SystemExit('本离线依赖包要求 Linux x86_64、Python 3.11；其他环境请重新准备依赖包')
        command(sys.executable,'-m','venv',ROOT/'.venv')
        command(ROOT/'.venv/bin/python','-m','pip','install','--disable-pip-version-check','--no-index','--find-links',ROOT/'wheelhouse','-r',ROOT/'backend/requirements.txt')
        for f in ['bin/lcr','lcr/dist/lcr-linux-x64']:(ROOT/f).chmod(0o755)
        command(ROOT/'bin/lcr','version')
    restore_repositories(cases)
    print('仓库提交与历史验证完成；未调用模型。')
if __name__=='__main__':main()
