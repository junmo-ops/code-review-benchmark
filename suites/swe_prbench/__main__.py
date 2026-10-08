import argparse,json
from pathlib import Path
from .evaluator import evaluate,report

def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['eval','report']);p.add_argument('--run',type=Path,required=True)
    args=p.parse_args();config=json.loads((args.run/'run-config.json').read_text(encoding='utf-8'))
    from suites.registry import resolve_run_suite
    if resolve_run_suite(config).id!='swe_prbench':raise ValueError('评分批次不是 SWE-PRBench')
    if args.stage=='eval':evaluate(args.run,config)
    else:report(args.run,config)
if __name__=='__main__':main()
