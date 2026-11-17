"""Explicit database provisioning and read-only status; not a dashboard replacement."""
import argparse
import json
from .database import Database
from .queue import CaseQueue


def main():
    parser = argparse.ArgumentParser(description='多实例调度管理（建表与故障排查；日常配置由 Web 启动自动同步）')
    parser.add_argument('--config', default='database.local.json', help='未设置 BENCH_DB_USER/PASSWORD 时使用的本地数据库 JSON 文件')
    parser.add_argument('--scope', required=True, help='环境标识，例如 st 或 dev')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('initialize', help='显式创建调度表，需要建表权限')
    settings = commands.add_parser('configure')
    settings.add_argument('--global-limit', type=int, required=True)
    settings.add_argument('--worker-limit', type=int, default=5)
    settings.add_argument('--paused', action='store_true')
    pool = commands.add_parser('pool')
    pool.add_argument('name')
    pool.add_argument('--limit', type=int, required=True)
    status = commands.add_parser('status')
    status.add_argument('run_id')
    args = parser.parse_args()
    database = Database.from_config(args.config)
    queue = CaseQueue(database, args.scope)
    if args.command == 'initialize':
        database.initialize()
    elif args.command == 'configure':
        queue.configure(args.global_limit, args.worker_limit, paused=args.paused)
    elif args.command == 'pool':
        queue.configure_pool(args.name, args.limit)
    else:
        print(json.dumps(queue.tasks(args.run_id), ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
