import argparse,json
from pathlib import Path
from suites.registry import resolve_run_suite
from .evaluator import evaluate,report

def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['eval','report']);p.add_argument('--run',type=Path,required=True)
    a=p.parse_args();config=json.loads((a.run/'run-config.json').read_text(encoding='utf-8'))
    if resolve_run_suite(config).id!='swe_prbench_llm':raise ValueError('不是内部大模型裁判批次')
    if a.stage=='eval':evaluate(a.run,config)
    else:report(a.run,config)
if __name__=='__main__':main()
