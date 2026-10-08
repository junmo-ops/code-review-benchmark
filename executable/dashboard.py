"""UI lifecycle for the single-case c-CRAB experiment; no scoring changes."""
from runner.runtime import data_root, config_root, docker_context

import argparse
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from runner.storage import ROOT, save

FILES = ['executable/ccrab_run.py', 'executable/deepseek_repair.py', 'executable/dashboard.py',
         'runner/runtime.py', 'executable/ccrab.py', 'reviewers/lcr/adapter.py', 'runner/process.py',
         'data/ccrab-pilot1.jsonl', 'data/sources/ccrab/provenance.json']

def identity(root, env):
    model = env.get('LCR_LLM_MODEL', '')
    if 'deepseek' not in model.lower() or not all(env.get(k) for k in ('LCR_LLM_TOKEN', 'LCR_LLM_BASE_URL')):
        raise ValueError('c-CRAB 当前仅支持已配置的 DeepSeek 检视与修复模型')
    return dict(review_model=model, endpoint_sha256=hashlib.sha256(env['LCR_LLM_BASE_URL'].encode()).hexdigest(),
                frozen_files={f:hashlib.sha256((root/f).read_bytes()).hexdigest() for f in FILES})

def verify(root, cfg, env=None):
    if not cfg.get('ui_managed'):
        raise ValueError('历史 CLI 批次仅供查看；重新执行请新建批次')
    for f, digest in cfg['frozen_files'].items():
        if f not in FILES or hashlib.sha256((root/f).read_bytes()).hexdigest()!=digest:
            raise ValueError('代码或案例清单已变化，请新建批次')
    if set(cfg['frozen_files']) != set(FILES):
        raise ValueError('冻结记录不完整，请新建批次')
    if env is not None:
        current=identity(root, env)
        if any(current[k]!=cfg[k] for k in current):
            raise ValueError('模型、接口或执行代码与创建时不同，请新建批次')

def main():
    p=argparse.ArgumentParser()
    p.add_argument('stage',choices=['init','check','run'])
    p.add_argument('--run-id',required=True)
    args=p.parse_args()
    from webui.service import NAME
    if not NAME.fullmatch(args.run_id):p.error('invalid run id')
    folder=data_root(ROOT)/'runs/ccrab'/args.run_id
    if args.stage=='init':
        from executable.ccrab import load
        row,_=load()
        cfg=dict(identity(ROOT,os.environ),ui_managed=True,instance_id=row['instance_id'],repo=row['repo'],
                 reviewer='lcr',suite={'id':'ccrab'},created_at=datetime.now(timezone.utc).isoformat())
        folder.mkdir(parents=True,exist_ok=False)
        save(folder/'run-config.json',cfg)
        print('c-CRAB 批次已创建；未调用模型。')
        return
    cfg=json.loads((folder/'run-config.json').read_text())
    verify(ROOT,cfg,os.environ if args.stage=='run' else None)
    if args.stage=='check':
        # Delegate through the existing CLI so its exit code remains authoritative.
        import subprocess,sys
        raise SystemExit(subprocess.call([sys.executable,str(ROOT/'ccrab.py'),'check']))
    if set(f.name for f in folder.iterdir()) != {'run-config.json'}:
        raise ValueError('此批次已有执行痕迹；请查看结果或新建批次，不覆盖或自动重跑')
    import sys
    from executable import ccrab_run
    sys.argv=[sys.argv[0],'run','--run-id',args.run_id,'--profile','environment','--initialized']
    ccrab_run.main()

if __name__=='__main__':
    import signal
    def interrupt(signum, frame):raise KeyboardInterrupt
    for s in (signal.SIGTERM,signal.SIGHUP):signal.signal(s,interrupt)
    main()
