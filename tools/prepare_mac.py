"""Prepare benchmark runtime on macOS without modifying LCR source."""
from pathlib import Path
import argparse,json,platform,shutil,subprocess
ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "backend/src"))
LAUNCHER='''#!/usr/bin/env python3
from pathlib import Path
import os,sys
root=Path(__file__).resolve().parents[1]
args=sys.argv[1:]
index=1 if args and args[0] in ('review','r','scan','s') else 2 if len(args)>1 and args[:2]==['rules','check'] else None
if index is not None:args[index:index]=['--rule',str(root/'lcr/rules/rule.json')]
binary=root/'lcr/dist/lcr-darwin-arm64'
os.execv(str(binary),[str(binary),*args])
'''
def main():
 p=argparse.ArgumentParser();p.add_argument('--binary',required=True);p.add_argument('--venv',required=True);args=p.parse_args()
 if platform.system()!='Darwin' or platform.machine()!='arm64':p.error('此本地入口只支持 macOS arm64')
 if not (ROOT/'bundles/cline__cline.bundle').exists():p.error('先 prepare_assets.py 安装资源')
 if (ROOT/'runs').exists() and any((ROOT/'runs').glob('*/run-config.json')):p.error('已存在冻结批次，不能替换运行时；请使用独立检出目录')
 venv=Path(args.venv).resolve()
 subprocess.run([str(venv/'bin/python'),'-c','import openai,pydantic,tqdm; import sys; assert sys.version_info>=(3,10)'],check=True)
 target=ROOT/'.venv'
 if target.exists() or target.is_symlink():
  if target.resolve()!=venv:p.error('已有 .venv 与指定环境不同，停止覆盖')
 else:target.symlink_to(venv,target_is_directory=True)
 dest=ROOT/'lcr/dist/lcr-darwin-arm64';shutil.copy2(args.binary,dest);dest.chmod(0o755)
 launcher=ROOT/'bin/lcr';launcher.write_text(LAUNCHER);launcher.chmod(0o755)
 (ROOT/'local-runtime.json').write_text(json.dumps({'platform':platform.system(),'architecture':platform.machine(),'binary':dest.name,'launcher':'benchmark direct exec with packaged rule; mac simulation'},indent=2)+'\n')
 subprocess.run([str(launcher),'version'],check=True)
 print('Mac runtime 已准备；保留原检视二进制与打包规则，只使用本地启动入口。')
if __name__=='__main__':main()
