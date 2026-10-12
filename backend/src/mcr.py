"""Bounded local/cloud entry; configuration and subprocess lifecycle reused."""
from runner.runtime import data_root, config_root, docker_context

import argparse,datetime,json,os,sys
from pathlib import Path
from local import ALLOWED,execute
from runner.paths import PROJECT_ROOT as ROOT

def main():
    p=argparse.ArgumentParser(add_help=False)
    p.add_argument('--timeout-seconds',type=int,default=900)
    p.add_argument('--config',type=Path)
    args,rest=p.parse_known_args()
    if args.timeout_seconds<1:p.error('时限必须大于 0')
    env=dict(os.environ);config=args.config or config_root(ROOT)/'model.local.json'
    if args.config and not config.is_file():p.error('指定模型配置不存在')
    if config.exists():
        data=json.loads(config.read_text(encoding='utf-8'))
        if not isinstance(data,dict) or set(data)-ALLOWED or any(not isinstance(v,str) for v in data.values()):p.error('配置字段无效')
        env.update(data)
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    record=data_root(ROOT)/'local-command-logs'/('mcr-'+stamp+'.json')
    print('MCR 同步命令上限：',args.timeout_seconds,'秒；执行记录：',record,flush=True)
    raise SystemExit(execute([sys.executable,'-m','multiround.cli',*rest],env,args.timeout_seconds,record))
if __name__=='__main__':main()
