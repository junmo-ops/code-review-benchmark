"""Build deliveries from the current project only. Excludes local secrets/history/runtime."""
from pathlib import Path
import hashlib,io,json,tarfile,zipfile
ROOT=Path(__file__).resolve().parents[1]
SHELL=b'#!/bin/sh\nroot=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\nexec node "$root/lcr/bin/lcr.js" "$@"\n'

def main():
    assets={'bin/lcr':(SHELL,0o755)}
    for name in ['lcr/bin','lcr/rules','bundles','wheelhouse','server-wheelhouse']:
        for path in (ROOT/name).rglob('*'):
            if path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts:
                assets[str(path.relative_to(ROOT))]=(path.read_bytes(),path.stat().st_mode&0o777)
    for name in ['lcr/package.json','lcr/dist/lcr-linux-x64']:
        path=ROOT/name;assets[name]=(path.read_bytes(),path.stat().st_mode&0o777)
    manifest={name:hashlib.sha256(data).hexdigest() for name,(data,mode) in sorted(assets.items())}
    (ROOT/'assets-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    archive=ROOT.parent/'lcr-cloud-assets.tar.gz'
    with tarfile.open(archive,'w:gz') as t:
        for name,(data,mode) in sorted(assets.items()):
            item=tarfile.TarInfo(name);item.size=len(data);item.mode=mode;t.addfile(item,io.BytesIO(data))
    (ROOT/'assets-archive.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'\n')
    # Explicit roots; no secrets, session evidence, runtime artifacts or CLI source.
    source=[]
    for pattern in ['*.py','*.md','*.example.json']:
        source.extend(ROOT.glob(pattern))
    source.extend(ROOT/name for name in ['.gitignore','Dockerfile','.dockerignore','requirements.txt','assets-manifest.json','assets-archive.sha256','打开测评工作台.command','打开测评工作台.bat','bin/lcr.cmd'])
    for name in ['framework','data','tests','benchmark','tools','docs','examples','runner','reviewers','suites','vendor','evaluators','scoring','multiround','webui','executable','deployment','comparison']:
        source.extend(p for p in (ROOT/name).rglob('*') if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts)
    aacr_repos={json.loads(line)['repo'].replace('/','__') for line in (ROOT/'data/pilot5.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()}
    aacr_bundles=[]
    for slug in sorted(aacr_repos):
        candidates=[ROOT/'bundles'/(slug+suffix) for suffix in ('.bundle','.git.tar.gz')]
        existing=[path for path in candidates if path.is_file()]
        if not existing:raise SystemExit('缺少 AACR 案例仓库历史：'+slug)
        aacr_bundles.extend(existing)
    source.extend(aacr_bundles)
    zipped=ROOT.parent/'code-review-benchmark-source.zip'
    with zipfile.ZipFile(zipped,'w',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(set(source)):
            if path.is_file() and not path.is_symlink():z.write(path,str(Path(ROOT.name)/path.relative_to(ROOT)))
    with zipfile.ZipFile(zipped) as z:
        for bundle in aacr_bundles:
            if f'{ROOT.name}/{bundle.relative_to(ROOT).as_posix()}' not in z.namelist():
                raise SystemExit('源码 ZIP 缺少 AACR 案例 Git 历史：'+bundle.name)
    with tarfile.open(archive) as t:
        for name,sha in manifest.items():assert hashlib.sha256(t.extractfile(name).read()).hexdigest()==sha
    print('源码包：',zipped,zipped.stat().st_size)
    print('资源包：',archive,archive.stat().st_size)
    print('Linux 资源归档逐文件校验通过；源码 ZIP 含 AACR 案例历史，不含模型配置或令牌。Windows 运行时从行内 npm 与 Python 包源安装。')
if __name__=='__main__':main()
