"""Join an offline official HF snapshot without dropping its references/contexts."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "backend/src"))
sys.path.insert(0,str(ROOT))
from suites.swe_prbench.adapter import records

def main():
    p=argparse.ArgumentParser(description='导入已下载的 SWE-PRBench 数据快照，不访问网络')
    p.add_argument('--source',type=Path,required=True,help='包含 prs.jsonl、annotations/、contexts/ 的 dataset 目录')
    p.add_argument('--task-id',action='append',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():p.error('输出已存在，拒绝覆盖')
    rows={r['task_id']:r for r in [json.loads(s) for s in (a.source/'prs.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]}
    joined=[]
    for task in a.task_id:
        row=dict(rows[task]);row['annotation']=json.loads((a.source/'annotations'/f'{task}_human.json').read_text(encoding='utf-8'))
        row['contexts']={c:json.loads((a.source/'contexts'/f'config_{c}'/f'{task}.json').read_text(encoding='utf-8')) for c in 'ABC'}
        joined.append(row)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    temp=a.output.with_suffix(a.output.suffix+'.tmp')
    try:
        temp.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in joined),encoding='utf-8')
        records(temp);temp.replace(a.output)
    finally:temp.unlink(missing_ok=True)
    print(f'导入 {len(joined)} 例：{a.output}；正式运行前需记录下载版本及 SHA256 来源清单。')
if __name__=='__main__':main()
