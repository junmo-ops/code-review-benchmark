"""One-case c-CRAB adaptation: LCR + DeepSeek repair + released pytest oracle."""
from runner.runtime import data_root, config_root, docker_context

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from runner.storage import ROOT, save
from runner.contracts import ReviewCase
from executable.ccrab import load, SOURCE
from executable import deepseek_repair
from reviewers.lcr import adapter

REPO=data_root(ROOT)/'repos/networkx__networkx'
IMAGE='ccrab-networkx-pilot:local'


def command(args,folder,name,timeout=180):
    from runner.process import capture_process
    p=capture_process(args,folder,name,timeout=timeout,stdout_suffix='.stdout.txt')
    if p.returncode:raise RuntimeError(name+' failed; see persisted logs')
    return p.stdout


def snapshot(commit,dest):
    dest.mkdir(parents=True,exist_ok=False)
    raw=subprocess.run(['git','-C',str(REPO),'archive',commit],capture_output=True,check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive.getmembers():
            if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):raise ValueError('Unsupported archive member')
            if not (dest/member.name).resolve().is_relative_to(dest.resolve()):raise ValueError('Unsafe archive path')
        archive.extractall(dest,filter='data')


def docker(context,*args):
    return [shutil.which('docker') or '/opt/homebrew/bin/docker','--context',context,*args]


def image_build(context,folder):
    folder.mkdir(parents=True,exist_ok=False)
    # Minimal local ARM-compatible test runtime, deliberately not called an official image.
    (folder/'Dockerfile').write_text('FROM python:3.11-slim\nRUN pip install --no-cache-dir pytest==8.3.5\nWORKDIR /workspace\nENV PYTHONDONTWRITEBYTECODE=1\nENV PYTHONPATH=/workspace\n')
    command(docker(context,'build','-t',IMAGE,str(folder)),folder,'build',900)
    metadata=json.loads(command(docker(context,'image','inspect',IMAGE),folder,'inspect',30))[0]
    save(folder/'image.json',{'id':metadata['Id'],'architecture':metadata['Architecture'],'protocol':'local_image_not_official'})


def test(context,image,work,testpath,folder,name):
    folder.mkdir(parents=True,exist_ok=True)
    # Create separately so a timeout cannot leave an unknown container running.
    cid=command(docker(context,'create','--network','none','--memory','2g','--cpus','2',
        '--cap-drop','ALL','--security-opt','no-new-privileges','--mount',f'type=bind,src={work.resolve()},dst=/workspace,readonly',
        '--mount',f'type=bind,src={testpath.resolve()},dst=/oracle/test_case.py,readonly',image,
        'python','-m','pytest','/oracle/test_case.py','-x','-v','--tb=short','--no-header','-c','/dev/null','-p','no:cacheprovider'),folder,name+'-create',30).strip()
    try:
        from runner.process import capture_process
        capture_process(docker(context,'start','-a',cid),folder,name,timeout=150,stdout_suffix='.stdout.txt')
        info=json.loads(subprocess.run(docker(context,'inspect',cid),capture_output=True,text=True,check=True).stdout)[0]
        code=info['State']['ExitCode']
        if info['State']['Running'] or info['State'].get('OOMKilled'):raise RuntimeError('Container incomplete or OOM')
        xml_text=(folder/(name+'.stdout.txt')).read_text(encoding='utf-8')
        # Pytest code 1 plus an assertion failure distinguishes a test miss from collection/setup errors.
        if code==0:status='passed'
        elif code==1 and 'AssertionError' in xml_text and 'FAILED' in xml_text and 'ERROR' not in xml_text:status='failed_assertion'
        else:raise RuntimeError('Test execution error, not a benchmark miss')
        save(folder/(name+'.result.json'),{'status':status,'exit_code':code})
        return status
    finally:
        subprocess.run(docker(context,'rm','-f',cid),capture_output=True,timeout=30)


def main():
    p=argparse.ArgumentParser(description='c-CRAB DeepSeek 单例适配实验')
    p.add_argument('stage',choices=['build','run'])
    p.add_argument('--context',default=docker_context())
    p.add_argument('--run-id',required=True)
    p.add_argument('--profile',default=str(config_root(ROOT)/'model.local.json'))
    p.add_argument('--initialized',action='store_true')
    args=p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.run_id):p.error('invalid run id')
    folder=data_root(ROOT)/'runs/ccrab'/args.run_id
    if args.stage=='build':image_build(args.context,folder);return
    if args.initialized:
        from executable.dashboard import verify
        cfg=json.loads((folder/'run-config.json').read_text())
        verify(ROOT,cfg,os.environ)
        if {p.name for p in folder.iterdir()} != {'run-config.json'}:raise ValueError('批次已执行，请新建批次')
        (folder/'execution.started').touch(exist_ok=False)
    else:
        folder.mkdir(parents=True,exist_ok=False)
    result={'status':'running','protocol':'ccrab_deepseek_local_v1','score':None,'review_executed':False,'repair_executed':False,'tests_executed':False}
    save(folder/'status.json',result)
    try:
        row,provenance=load()
        profile=dict(os.environ) if args.profile=='environment' else json.loads(Path(args.profile).read_text(encoding='utf-8'))
        if 'deepseek' not in profile['LCR_LLM_MODEL'].lower():raise ValueError('This experiment requires DeepSeek')
        for k in ('LCR_LLM_MODEL','LCR_LLM_TOKEN','LCR_LLM_BASE_URL'):
            if not profile.get(k):raise ValueError('Missing model configuration')
        image=command(docker(args.context,'image','inspect','--format','{{.Id}}',IMAGE),folder,'image',30).strip()
        save(folder/'config.json',{'protocol':result['protocol'],'instance_id':row['instance_id'],'upstream':provenance['revision'],
             'review_model':profile['LCR_LLM_MODEL'],'repair_model':profile['LCR_LLM_MODEL'],'repair_max_rounds':deepseek_repair.MAX_ROUNDS,'repair_temperature':0,'repair_max_tokens':8192,'repair_request_timeout_seconds':180,'repair_sdk_retries':0,
             'image_id':image,'model_endpoint_sha256':hashlib.sha256(profile['LCR_LLM_BASE_URL'].encode()).hexdigest(),
             'code_hashes':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ['executable/ccrab_run.py','executable/deepseek_repair.py']}})
        command(docker(args.context,'run','--rm','--network','none',image,'python','-c','import sys,pytest; print(sys.version); print("pytest",pytest.__version__)'),folder,'runtime-version',30)
        testpath=SOURCE/'testgen'/row['instance_id']/'test_review_comment_0.py'
        # Run oracle controls before any paid review. Never put them in the review/repair workspace.
        with tempfile.TemporaryDirectory(prefix='ccrab-controls-') as tmp:
            base=Path(tmp)/'before';gold=Path(tmp)/'after'
            snapshot(row['commit_to_review']['head_commit'],base);snapshot(row['merged_commit'],gold)
            before=test(args.context,image,base,testpath,folder/'controls','before')
            after=test(args.context,image,gold,testpath,folder/'controls','after')
            if before!='failed_assertion' or after!='passed':raise RuntimeError('Oracle control mismatch; stopping before model calls')
        result['tests_executed']=True
        from suites.codefuse_cr.adapter import normalize_patch
        actual=subprocess.run(['git','-C',str(REPO),'diff','--no-ext-diff','--no-textconv','--no-color',row['base_commit']+'...'+row['commit_to_review']['head_commit'],'--'],capture_output=True,text=True,check=True).stdout
        if normalize_patch(actual)!=normalize_patch(row['commit_to_review']['patch_to_review']):raise ValueError('Review diff does not match pinned case')
        os.environ.update({k:profile[k] for k in ('LCR_LLM_MODEL','LCR_LLM_TOKEN','LCR_LLM_BASE_URL')})
        os.environ['PATH']=str(ROOT/'bin')+os.pathsep+os.environ.get('PATH','')
        os.environ['LCR_DIAGNOSTICS_DIR']=str(folder/'diagnostics')
        case=ReviewCase(row['instance_id'],row['repo'],row['base_commit'],row['commit_to_review']['head_commit'])
        adapter.REPO=REPO;adapter.CASE_JOBS=1;adapter.LCR_TIMEOUT_MINUTES=10
        adapter.check_repos([case])
        raw=adapter.run_lcr(case,False,folder)
        answer=adapter.convert(case,raw);save(folder/'answer.json',answer);result['review_executed']=True
        reviews=answer['review']['comments']
        if not reviews:
            result.update(status='complete_no_findings',test_pass_rate=0.0,repair_executed=False,note='完整空答卷；未调用修复模型，测试通过率按本地约定记 0，非官方脚本实际执行得分')
        else:
            # Persistent answer/logs are outside the source-only workspace provided to repair tools.
            work=folder/'repair-workspace';snapshot(case.head_commit,work)
            deepseek_repair.run(work,reviews,row['commit_to_review']['patch_to_review'],profile,folder/'repair')
            result['repair_executed']=True
            with tempfile.TemporaryDirectory(prefix='ccrab-diff-') as tmp:
                original=Path(tmp)/'original';snapshot(case.head_commit,original)
                diff=subprocess.run(['git','diff','--no-index','--',str(original),str(work)],capture_output=True,text=True)
                if diff.returncode not in (0,1):raise RuntimeError('Cannot capture repair diff')
                (folder/'repair.patch').write_text(diff.stdout,encoding='utf-8')
            outcome=test(args.context,image,work,testpath,folder/'evaluation','repaired')
            result.update(status='complete',test_pass_rate=1.0 if outcome=='passed' else 0.0,score=None)
    except BaseException as exc:
        result.update(status='interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',error_type=type(exc).__name__)
        raise
    finally:
        save(folder/'status.json',result)
        (folder/'report.zh-CN.md').write_text('# c-CRAB 单例适配报告\n\n'+
            '协议：LCR 检视 + DeepSeek 文件编辑 Agent + 本地容器 + 官方发布测试。非官方固定执行器复现。\n\n'+
            '\n'.join(f'- {dict(status="执行状态",protocol="评分协议",score="语义分数（不适用）",review_executed="检视已完成",repair_executed="修复已完成",tests_executed="测试对照已完成",test_pass_rate="发布测试通过率",note="说明",error_type="错误类型").get(k,k)}: {v}' for k,v in result.items())+
            '\n\n本例测试检查源码字符串的指定写法，不代表一般行为正确性；失败、超时和测试未执行不计为有效得分。\n',encoding='utf-8')
        print('报告：',folder/'report.zh-CN.md',flush=True)

if __name__=='__main__':
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupt)
    main()
