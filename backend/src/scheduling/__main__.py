"""Explicit database provisioning and read-only status; not a dashboard replacement."""
import argparse
import json
from .config import cluster_options, load_local_cluster
from .database import Database
from .queue import CaseQueue


def main():
    parser = argparse.ArgumentParser(description='多实例调度管理（建表与故障排查；日常配置由 Web 启动自动同步）')
    parser.add_argument('--config', help='兼容旧版数据库 JSON 文件；使用此项时须指定 --scope')
    parser.add_argument('--scope', help='覆盖配置中的 scope')
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
    try:
        queue = _queue(args)
    except ValueError as error:
        parser.error(str(error))
    if args.command == 'initialize':
        queue.database.initialize()
    elif args.command == 'configure':
        queue.configure(args.global_limit, args.worker_limit, paused=args.paused)
    elif args.command == 'pool':
        queue.configure_pool(args.name, args.limit)
    else:
        print(json.dumps(queue.tasks(args.run_id), ensure_ascii=False, indent=2, default=str))


def _queue(args):
    if args.config is None:
        load_local_cluster()
    elif args.scope is None:
        raise ValueError('使用 --config 时须指定 --scope')
    scope = args.scope if args.scope is not None else cluster_options().get('scope')
    if scope is None:
        raise ValueError('请在集群配置中设置 scope，或传入 --scope')
    return CaseQueue(Database.from_config(args.config), scope)


if __name__ == '__main__':
    main()
