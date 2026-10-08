"""Read-only deployment checks; no model requests and no credential values."""
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from runner.runtime import data_root,config_root,docker_context

def main():
    checks={}
    checks['Linux']=platform.system()=='Linux'
    checks['Python 3.11+']=sys.version_info>=(3,11)
    for name in ('git','node'):
        checks[name]=bool(shutil.which(name))
    for name in ('flask','gunicorn'):
        import importlib.util
        checks[name]=bool(importlib.util.find_spec(name))
    checks['服务模式']=os.getenv('BENCH_SERVER_MODE')=='1'
    checks['持久化目录可写']=data_root(ROOT).is_dir() and os.access(data_root(ROOT),os.W_OK)
    checks['模型配置目录存在']=config_root(ROOT).is_dir()
    for name in ('BENCH_PROXY_TOKEN','BENCH_CSRF_TOKEN'):
        checks[name+' 已设置']=len(os.getenv(name,''))>=32
    checks['HTTPS 站点配置']=os.getenv('BENCH_PUBLIC_URL','').startswith('https://')
    lcr=ROOT/'bin/lcr'
    try:
        proc=subprocess.run([str(lcr),'version'],capture_output=True,text=True,timeout=15)
        checks['LCR 可执行']=proc.returncode==0
        if proc.returncode==0:print(proc.stdout.strip())
    except (OSError,subprocess.TimeoutExpired):checks['LCR 可执行']=False
    for name,ok in checks.items():print(('通过' if ok else '未就绪')+'：'+name)
    print('Docker（仅 c-CRAB 需要）：'+('已安装；context='+docker_context() if shutil.which('docker') else '未安装'))
    print('这不验证模型可达性、认证网关、服务器重启恢复或案例完整性。')
    return 0 if all(checks.values()) else 2
if __name__=='__main__':raise SystemExit(main())
