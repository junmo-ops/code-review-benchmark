"""Internal case subprocess; the parent command owns the batch lock."""
import json
from pathlib import Path
from runner import cloud


def main():
    args = cloud._arguments()
    run = Path(args.runs_root).resolve() / args.run_id
    cfg = json.loads((run / 'run-config.json').read_text(encoding='utf-8'))
    if args.stage != 'review' or not args.instance or cfg.get('reviewer_id') != 'server_agent':
        raise ValueError('内部命令仅用于服务端 Agent 单案例检视')
    items = cloud.checked_instances(run / 'dataset.jsonl', cfg['suite']['id'])
    selected = next((item for item in items if item.instance_id == args.instance), None)
    if selected is None:
        raise ValueError('案例不在冻结清单中')
    args.agent_child = True
    with cloud.exclusive(run / 'agent-case-locks' / selected.safe_id):
        cloud._execute_run(args, run)


if __name__ == '__main__':
    main()
