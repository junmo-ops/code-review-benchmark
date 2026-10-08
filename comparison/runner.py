"""Serial comparison; one failed case does not erase evidence or retry models."""
import argparse
import asyncio
import hashlib
import json
import subprocess
import time
from pathlib import Path
from runner.storage import ROOT, save
from webui.service import Dashboard
from suites.registry import get_suite
from reviewers.registry import get_reviewer
from .scorer import score_case, identity, PROTOCOL, PARAMETERS
from .report import build


def execute(command,env,limit,path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8') as log:
        process=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        try:return process.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            from local import terminate
            terminate(process);raise TimeoutError('阶段执行超时') from None


def run(app,folder):
    m=json.loads((folder/'experiment.json').read_text());suite=get_suite(m['suite'])
    if m['protocol']!=PROTOCOL or m['parameters']!=PARAMETERS or m['code_identity']!=identity() or m['suite_identity']!=suite.identity() or m['tool_identity']!=get_reviewer('lcr').tool_identity():raise ValueError('排队期间协议、案例适配或工具发生变化；请新建实验')
    source=app.root/'data'/m['dataset']
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=m['dataset_sha256'] for p in (source,folder/'dataset.jsonl')):raise ValueError('案例清单发生变化')
    for g in m['groups']:
        if hashlib.sha256((app.configs/g['profile']).read_bytes()).hexdigest()!=g['config_sha256']:raise ValueError('实验模型配置发生变化')
        if app.run_dir(g['key']).exists():raise ValueError('已有分组执行痕迹，不自动重跑；请新建实验')
    failed=False
    for g in m['groups']:
        root=app.run_dir(g['key']);profile=g['profile']
        spec=dict(suite=m['suite'],dataset=m['dataset'],reviewer='lcr',evaluator='martian',timeout_minutes=m['timeout_minutes'],max_tokens=8192)
        print('开始模型组：',g['label'],flush=True)
        try:
            for stage in ('init','check'):
                command,env,limit=app.command(g['key'],stage,profile,init=spec if stage=='init' else None)
                if execute(command,env,limit,folder/'logs'/f'{g["key"]}-{stage}.log')!=0:raise ValueError('初始化或环境检查失败')
                if stage=='init':save(root/'comparison-protocol.json',dict(protocol=PROTOCOL,experiment=m['key'],parameters=PARAMETERS))
            save(root/'comparison-protocol.json',dict(protocol=PROTOCOL,experiment=m['key'],parameters=PARAMETERS))
            for case_id in m['case_ids']:
                target=root/'comparison-status'/f'{case_id.replace("/","__")}.json'
                status=dict(status='reviewing');save(target,status);build(folder)
                started=time.monotonic()
                try:
                    print(g['label'],case_id,'检视中',flush=True)
                    command,env,limit=app.command(g['key'],'review',profile,task=case_id)
                    code=execute(command,env,m['case_timeout_seconds'],folder/'logs'/f'{g["key"]}-{case_id.replace("/","__")}.log')
                    status['review_seconds']=round(time.monotonic()-started,3)
                    if code:raise ValueError('检视失败，参见案例日志')
                    status['status']='scoring';save(target,status);build(folder)
                    asyncio.run(asyncio.wait_for(score_case(root,m['suite'],case_id,env),timeout=m['judge_case_timeout_seconds']))
                    status['status']='complete'
                except Exception as exc:
                    failed=True
                    status.update(status='score_failed' if status['status']=='scoring' else 'review_failed',error=type(exc).__name__,review_seconds=status.get('review_seconds',round(time.monotonic()-started,3)))
                    print('案例未完成：',case_id,type(exc).__name__,flush=True)
                finally:save(target,status);build(folder)
        except Exception as exc:
            failed=True
            for case_id in m['case_ids']:
                save(root/'comparison-status'/f'{case_id.replace("/","__")}.json',dict(status='setup_failed',error=type(exc).__name__))
            print('模型组准备失败：',g['label'],type(exc).__name__,flush=True)
        finally:build(folder)
    print('对比报告：',folder/'report.zh-CN.md',flush=True)
    return 1 if failed else 0


def main():
    p=argparse.ArgumentParser();p.add_argument('--key',required=True);args=p.parse_args()
    app=Dashboard(load_jobs=False);folder=app.run_dir(args.key)
    raise SystemExit(run(app,folder))

if __name__=='__main__':main()
