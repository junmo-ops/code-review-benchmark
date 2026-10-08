"""Install a verified offline asset archive in this checkout; no network or LLM."""
from pathlib import Path,PurePosixPath
import argparse,hashlib,json,tarfile,shutil
ROOT=Path(__file__).resolve().parents[1]

def main():
 p=argparse.ArgumentParser();p.add_argument('--archive',required=True);args=p.parse_args()
 archive=Path(args.archive)
 expected=(ROOT/'assets-archive.sha256').read_text().strip()
 if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:raise SystemExit('资源压缩包 SHA256 不一致，请使用对应版本资源包')
 manifest=json.loads((ROOT/'assets-manifest.json').read_text())
 with tarfile.open(archive) as t:
  members=t.getmembers()
  names=[m.name for m in members]
  if len(names)!=len(set(names)) or set(names)!=set(manifest):raise SystemExit('资源文件清单不一致')
  for member in members:
   path=PurePosixPath(member.name)
   if not member.isfile() or path.is_absolute() or '..' in path.parts:raise SystemExit('资源路径或类型不安全')
   data=t.extractfile(member).read()
   if hashlib.sha256(data).hexdigest()!=manifest[member.name]:raise SystemExit('资源校验失败：'+member.name)
   target=ROOT.joinpath(*path.parts)
   if target.is_symlink() or any(parent.is_symlink() for parent in target.parents if parent!=ROOT.parent):raise SystemExit('资源目标不能为符号链接')
   if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=manifest[member.name]:raise SystemExit('已有资源不同，停止覆盖：'+member.name)
   target.parent.mkdir(parents=True,exist_ok=True)
   target.write_bytes(data)
   if member.name in ('bin/lcr','lcr/dist/lcr-linux-x64'):target.chmod(0o755)
 print('离线资源已安装并校验；未调用模型。')
if __name__=='__main__':main()
