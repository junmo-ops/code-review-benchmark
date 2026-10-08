"""Scoring selection is independent of dataset/reviewer selection."""
import hashlib,subprocess,sys
from runner.storage import ROOT

PARAMETERS={'temperature':0,'max_tokens':8192,'timeout_seconds':180,'max_retries':0,
            'rounds':1,'concurrency':1,'dedup':'none_structured_findings','profile':'all_references',
            'protocol':'martian_offline_judge_adapter_v1'}

def identity():
    files=[p for name in ('evaluators','vendor/martian') for p in (ROOT/name).rglob('*')
           if p.is_file() and '__pycache__' not in p.parts and (p.suffix=='.py' or p.name in ('UPSTREAM.json','LICENSE'))]
    return {'id':'martian','files':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}}

def freeze(name,suite):
    if name=='original':return {'id':'original','protocol':'suite_native'}
    if name!='martian':raise ValueError('未知裁判方案')
    if suite.id not in ('aacr','swe_prbench','swe_prbench_llm'):raise ValueError('Martian 尚未适配此数据集')
    return {'id':'martian','identity':identity(),'parameters':dict(PARAMETERS)}

def verify(config):
    frozen=config.get('evaluator',{'id':'original'})
    if frozen['id']=='martian':
        if frozen['identity']!=identity() or frozen['parameters']!=PARAMETERS:raise ValueError('Martian 裁判版本或参数已变化，请另建批次')
    elif frozen['id']!='original':raise ValueError('未知裁判方案')
    return frozen['id']

def execute(stage,suite,cases,run,config):
    if verify(config)=='original':
        if stage=='eval':suite.evaluate(cases,run,config)
        else:suite.report(run,config)
    else:subprocess.run([sys.executable,'-m','evaluators.martian',stage,'--run',str(run)],cwd=ROOT,check=True)
