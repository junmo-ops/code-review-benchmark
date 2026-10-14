"""Private, isolated grading worker; public calls use cloud.py/local.py."""
import argparse,json
from pathlib import Path
from .evaluator import stage_eval,stage_report

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("stage",choices=["eval","report"])
    parser.add_argument("--run",required=True,type=Path)
    args=parser.parse_args()
    config=json.loads((args.run/"run-config.json").read_text(encoding="utf-8"))
    from suites.registry import resolve_run_suite
    suite=resolve_run_suite(config)
    if suite.id != "aacr":raise ValueError("评分任务不是 AACR")
    config = dict(config, **config.get("suite", {}).get("parameters", {}))
    if args.stage=="eval":stage_eval(None,args.run,config)
    else:stage_report(args.run,config)

if __name__=="__main__":main()
