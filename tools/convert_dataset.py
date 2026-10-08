"""Convert a registered suite's source to its execution dataset, without LLM calls."""
import argparse,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from suites.registry import get_suite

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--suite',required=True)
    p.add_argument('--input',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--limit',type=int)
    p.add_argument('--seed',type=int,default=42)
    args=p.parse_args()
    if args.output.exists():p.error('输出已存在，请选择新文件，避免覆盖冻结清单')
    get_suite(args.suite).convert(args.input.resolve(),args.output.resolve(),args.limit,args.seed)
if __name__=='__main__':main()
