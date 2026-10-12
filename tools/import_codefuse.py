"""Offline import of official CodeFuse/SWE-CARE test JSONL or Parquet."""
import argparse, hashlib, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "backend/src"))
sys.path.insert(0,str(ROOT))
from suites.codefuse_cr.adapter import records


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--instance',action='append')
    p.add_argument('--smallest',type=int,help='按 patch 字符数和 ID 排序，固定选前 N 例，仅用于接入验证')
    p.add_argument('--revision',required=True,help='官方数据 revision 完整 SHA；自定义数据填 unknown')
    args=p.parse_args()
    if bool(args.instance)==bool(args.smallest):p.error('只能选择 --instance 或 --smallest 其中之一')
    if args.smallest is not None and args.smallest<1:p.error('数量必须为正')
    manifest_path=args.output.with_suffix('.provenance.json')
    if args.output.exists() or manifest_path.exists():p.error('输出已存在，拒绝覆盖')
    if args.source.suffix=='.parquet':
        try:import pyarrow.parquet as pq
        except ImportError:p.error('仅转换 Parquet 需要 pyarrow；运行测评无需安装，可先在行外转换为 JSONL')
        rows=pq.read_table(args.source).to_pylist()
    else:
        records(args.source)
        with args.source.open(encoding='utf-8') as f:rows=[json.loads(x) for x in f if x.strip()]
    if args.instance:
        lookup={r['instance_id']:r for r in rows}
        if any(i not in lookup for i in args.instance):p.error('指定案例不存在')
        selected=[lookup[i] for i in args.instance]
        rule={'method':'explicit_ids','ids':args.instance}
    else:
        selected=sorted(rows,key=lambda r:(len(r['commit_to_review']['patch_to_review']),r['instance_id']))[:args.smallest]
        rule={'method':'smallest_patch_chars_then_instance_id','limit':args.smallest,'purpose':'integration_only_not_representative'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    temp=args.output.with_suffix('.tmp')
    try:
        temp.write_text(''.join(json.dumps(row,ensure_ascii=True)+'\n' for row in selected),encoding='utf-8')
        records(temp);temp.replace(args.output)
    finally:temp.unlink(missing_ok=True)
    manifest={'dataset':'inclusionAI/SWE-CARE','revision':args.revision,'split':'test',
              'source_sha256':hashlib.sha256(args.source.read_bytes()).hexdigest(),
              'source_rows':len(rows),'selection':rule,'selected_ids':[x['instance_id'] for x in selected],
              'dataset_sha256':hashlib.sha256(args.output.read_bytes()).hexdigest(),
              'note':'revision 为输入声明；官方来源认证需要核对原始下载哈希与来源清单'}
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'已导入 {len(selected)} 例：{args.output}；未调用模型。')

if __name__=='__main__':main()
