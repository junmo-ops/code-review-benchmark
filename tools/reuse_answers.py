"""Reuse complete review answers in a new scoring run with identical review inputs."""
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from runner.storage import save,validate_answer
from suites.registry import resolve_run_suite
from scoring.registry import verify

def reuse(source,target):
    source,target=Path(source).resolve(),Path(target).resolve()
    if source==target:raise ValueError('来源与目标批次不能相同')
    a=json.loads((source/'run-config.json').read_text());b=json.loads((target/'run-config.json').read_text())
    resolve_run_suite(a);suite=resolve_run_suite(b)
    verify(b)
    for key in ('dataset_sha256','reviewer_id','tool_hashes','review_model','review_endpoint_sha256'):
        if a.get(key)!=b.get(key):raise ValueError('检视输入或配置不一致：'+key)
    for run,cfg in ((source,a),(target,b)):
        if hashlib.sha256((run/'dataset.jsonl').read_bytes()).hexdigest()!=cfg['dataset_sha256']:raise ValueError('数据哈希不一致')
    if any((target/name).exists() for name in ('results','raw','judge','metrics','answer-reuse.json')):
        raise ValueError('目标批次已有执行或评分记录，拒绝覆盖')
    answers=[];evidence=[]
    for case in suite.load_cases(target/'dataset.jsonl'):
        path=source/'results'/f'{case.safe_id}.json';value=validate_answer(case,path)
        if value.get('repo')!=case.repo:raise ValueError('答卷仓库不一致')
        answers.append((case.safe_id,value));evidence.append({'instance_id':case.instance_id,'source_file':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    for name,value in answers:save(target/'results'/f'{name}.json',value)
    save(target/'answer-reuse.json',{'source_run':str(source),'source_config_sha256':hashlib.sha256((source/'run-config.json').read_bytes()).hexdigest(),'answers':evidence,'review_rerun':False})
    print(f'复用 {len(answers)} 份完整答卷；未调用检视模型，未复制旧评分。')

def main():
    p=argparse.ArgumentParser();p.add_argument('--source-run',type=Path,required=True);p.add_argument('--target-run',type=Path,required=True);a=p.parse_args();reuse(a.source_run,a.target_run)
if __name__=='__main__':main()
