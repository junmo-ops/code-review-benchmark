"""Synchronous Linux benchmark jobs. No background daemon and no model-driven edits."""

from __future__ import annotations
from runner.runtime import data_root, config_root, docker_context
import argparse
from datetime import datetime, timezone
from runner.storage import save
from contextlib import contextmanager
from runner.locking import acquire, release
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import sys
import tarfile

from runner.paths import PROJECT_ROOT as ROOT
sys.path.insert(0, str(ROOT))
from reviewers.registry import get_reviewer
from suites.registry import get_suite, ensure_compatible, resolve_run_suite
from scoring import registry as evaluators
adapter = get_reviewer("lcr")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tool_identity():
    return adapter.tool_identity()


def checked_instances(dataset, suite_id="aacr"):
    items = get_suite(suite_id).load_cases(dataset)
    if not items:
        raise ValueError('案例集不能为空')
    names = set()
    for item in items:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', item.repo) or '..' in item.repo.split('/'):
            raise ValueError('仓库名必须是 owner/repo')
        if not re.fullmatch(r'[A-Za-z0-9_.@-]+', item.safe_id) or item.safe_id in ('.','..') or item.safe_id in names:
            raise ValueError('案例文件名不安全或存在冲突：'+item.instance_id)
        names.add(item.safe_id)
        for ref in (item.base_commit, item.head_commit):
            if not re.fullmatch(r'[0-9a-fA-F]{40}', ref):
                raise ValueError('提交必须固定为完整 SHA：'+item.instance_id)
    return items


def repo_path(root, item):
    return Path(root) / item.repo.replace('/', '__')


@contextmanager
def exclusive(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory/'.cloud.lock').open('a+b') as lock:
        try:
            acquire(lock)
        except BlockingIOError:
            raise ValueError('本轮已有执行中的命令；禁止重复启动') from None
        try:
            yield
        finally:
            release(lock)


def configure(cfg, run):
    adapter.configure(cfg, run)


def states(items, run):
    result = []
    for item in items:
        answer = run/'results'/(item.safe_id+'.json')
        execution = run/'raw'/(item.safe_id+'.execution.json')
        state = 'pending'
        if answer.exists():
            try:
                saved = adapter.validate_answer(item, answer)
                failure_check = getattr(adapter, 'saved_session_failures', None)
                session_id = (saved.get('review') or {}).get('session_id')
                state = ('answer_with_item_failures'
                         if failure_check and failure_check(item, run, session_id)
                         else 'complete')
            except ValueError:
                state = 'invalid_answer'
        elif execution.exists():
            try:
                rec = json.loads(execution.read_text(encoding='utf-8'))
                if rec.get('status') == 'exited' and rec.get('exit_code') == 0:
                    state = 'recoverable_check_required'
                else:
                    state = rec.get('status', 'unknown')
            except (ValueError, OSError):
                state = 'unknown'
            if state in ('starting','running'):
                state = 'unfinished_check_required'
        elif (run/'raw'/(item.safe_id+'.outcome.json')).exists():
            state = 'failed'
        result.append({'instance_id': item.instance_id, 'status': state})
    return result


def _model_endpoints():
    def endpoint(key):
        value = os.getenv(key)
        return hashlib.sha256(value.encode()).hexdigest() if value else None
    return dict(judge_model=os.getenv('JUDGE_MODEL') or None,
                judge_endpoint_sha256=endpoint('JUDGE_BASE_URL'),
                review_endpoint_sha256=endpoint('LCR_LLM_BASE_URL'))


def _runtime_mode():
    if os.getenv('BENCH_SERVER_MODE'):
        return 'server'
    if (ROOT/'local-runtime.json').exists():
        return 'windows_local' if platform.system() == 'Windows' else 'mac_cloud_simulation'
    return 'linux_cloud'


def _execution_limits(args, reviewer_id):
    case_jobs = getattr(args, 'case_jobs', None)
    if case_jobs is None: case_jobs = 1
    if type(case_jobs) is not int or not 1 <= case_jobs <= 16:
        raise ValueError('案例并发必须为 1–16 的整数')
    if reviewer_id != 'lcr' and case_jobs != 1:
        raise ValueError('案例并发仅适用于 LCR')
    max_case_retries = getattr(args, 'max_case_retries', None)
    if max_case_retries is None:
        max_case_retries = 1 if reviewer_id == 'lcr' else 0
    if type(max_case_retries) is not int or not 0 <= max_case_retries <= 2:
        raise ValueError('失败案例自动重试次数必须为 0–2')
    if reviewer_id != 'lcr' and max_case_retries:
        raise ValueError('失败案例自动重试当前仅支持 LCR')
    return case_jobs, max_case_retries


def init_run(args, run):
    if (run/'run-config.json').exists():
        raise ValueError('批次已经存在，请沿用现有批次或使用新名称')
    if not args.dataset or not args.model:
        raise ValueError('init 需要 --dataset 和 --model（不是令牌）')
    dataset = Path(args.dataset).resolve()
    suite = get_suite(getattr(args, "suite", None) or "aacr")
    reviewer_id = getattr(args, "reviewer", None) or "lcr"
    reviewer = get_reviewer(reviewer_id)
    ensure_compatible(suite, reviewer)
    items = checked_instances(dataset, suite.id)
    if reviewer_id=='cline_skill' and len(items)!=1:raise ValueError('Cline + Skill 当前仅支持单案例试跑，请选择单例清单')
    case_jobs, max_case_retries = _execution_limits(args, reviewer_id)
    from reviewers.lcr.options import freeze_options
    lcr_options = freeze_options() if reviewer_id == 'lcr' else {}
    repo_root = Path(args.repos_root or data_root(ROOT)/'repos').resolve()
    cfg = {'schema_version':2, 'reviewer_id':reviewer_id,
           'evaluator':evaluators.freeze(getattr(args,'evaluator','original'),suite),
           'suite':{'id':suite.id, 'identity':suite.identity(), 'parameters':dict(suite.parameters), 'provenance':suite.provenance(dataset)}, 'dataset_sha256':digest(dataset), 'selected_instances':[i.instance_id for i in items],
           'reviewer':reviewer_id, 'tool_hashes':reviewer.tool_identity(),
           'review_model':args.model,
           **_model_endpoints(),
           'repos_root':str(repo_root), 'case_jobs':case_jobs,'concurrency':1,
           'max_case_retries':max_case_retries,
           'timeout_minutes':args.timeout_minutes, 'lcr_options':lcr_options, 'max_tools':lcr_options.get('max_tools', 50),'workspace_isolation':True,
           'repository_mode':'per_case_git_worktrees',
           'process_timeout_policy':'lcr_options.case_timeout_seconds or (changed_files * timeout_minutes * 60 + 120) per attempt; total includes max_case_retries and backoff',
           'execution_platform':platform.system(), 'execution_architecture':platform.machine(),
           'python_version':platform.python_version(),
           'runtime_mode':_runtime_mode(),
           'created_at':datetime.now(timezone.utc).isoformat()}
    if reviewer_id=='server_agent':
        remote=reviewer.load_config()
        cfg.update(simulation=remote['simulation'],repository_mode='remote_fixed_commit_ranges',process_timeout_policy='whole_case_wait_timeout',diff_mode=remote['diff_mode'],review_endpoint_sha256=None,requested_model_only=True)
    if reviewer_id=='cline_skill':
        cfg.update(process_timeout_policy='whole_case_timeout_plus_20_seconds',repository_mode='head_snapshot_plus_merge_base_diff',pilot_only=True)
    (run/'dataset.jsonl').write_bytes(dataset.read_bytes())
    save(run/'run-config.json',cfg)
    print(f'案例集：{suite.name}；适配器版本：{suite.version}')
    print(f'批次已冻结：{run}\n案例数：{len(items)}；案例并发：{case_jobs}；每案例文件并发：1')


def require_model(cfg):
    adapter.require_model(cfg)


def main():
    global adapter
    p=argparse.ArgumentParser(description='LCR 测评入口；支持受控的案例并发，不启动后台进程')
    p.add_argument('stage',choices=['init','check','status','next','preview','review','recover','eval','report','export'])
    p.add_argument('--run-id',required=True)
    p.add_argument('--runs-root',default=str(data_root(ROOT)/'runs'),help='平台确认的持久化存储路径')
    p.add_argument('--dataset')
    p.add_argument('--suite',help='仅 init 指定，默认 aacr；未知案例集会拒绝运行')
    p.add_argument('--evaluator',choices=['martian','original'],default=None,help='仅 init 指定，默认 martian；original 使用案例集原裁判')
    p.add_argument('--reviewer',help='仅 init 指定，默认 lcr')
    p.add_argument('--repos-root')
    p.add_argument('--model')
    p.add_argument('--timeout-minutes',type=int,default=None,help='仅 init 生效，默认30分钟')
    p.add_argument('--case-jobs',type=int,default=None,help='仅 init 指定，LCR 案例并发 1–16，默认1')
    p.add_argument('--max-case-retries',type=int,default=None,help='仅 init 指定，失败案例自动重试 0–2 次，默认1')
    p.add_argument('--instance',help='preview/recover 必填；review 不指定时调度所有未完成案例')
    p.add_argument('--external-timeout-seconds',type=int,help='实际为本次工具命令设置的时限；review 必填')
    p.add_argument('--retry',action='store_true',help='确认上次任务已终止后，显式重跑失败案例')
    args=p.parse_args()
    if args.stage!='init' and (args.suite is not None or args.reviewer is not None or args.evaluator is not None):
        p.error('案例集和检视工具已冻结；调整时请新建批次')
    if args.stage!='init' and args.timeout_minutes is not None:
        p.error('超时已在 init 冻结；调整时请新建批次')
    if args.stage!='init' and (args.case_jobs is not None or args.max_case_retries is not None):
        p.error('案例并发和自动重试次数已在 init 冻结；调整时请新建批次')
    if args.evaluator is None: args.evaluator='martian'
    if args.timeout_minutes is None: args.timeout_minutes=30
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.run_id) or args.timeout_minutes<1:
        p.error('批次名或时限无效')
    run=Path(args.runs_root).resolve()/args.run_id
    with exclusive(run):
        if args.stage=='init':
            init_run(args,run);return
        cfg=json.loads((run/'run-config.json').read_text(encoding='utf-8'))
        if digest(run/'dataset.jsonl')!=cfg['dataset_sha256']:
            raise ValueError('冻结数据集已变化')
        suite=resolve_run_suite(cfg, verify=args.stage not in ('status','next','export'))
        adapter=get_reviewer(cfg.get('reviewer_id','lcr'))
        ensure_compatible(suite,adapter)
        if 'suite' not in cfg:
            print('历史批次按 AACR 兼容读取；当时未冻结评分器版本。',file=sys.stderr)
        items=checked_instances(run/'dataset.jsonl',suite.id)
        configure(cfg,run)
        if cfg.get('reviewer_id', 'lcr') == 'lcr' and args.stage in ('status','review','eval','report'):
            adapter.write_summary(items,run)
        snapshot=states(items,run)
        if args.stage=='status':
            save(run/'cloud-status.json',snapshot)
            print(json.dumps(snapshot,ensure_ascii=False,indent=2));return
        if args.stage=='export':
            destination=run.parent/(args.run_id+'-results.tar.gz')
            with tarfile.open(destination,'w:gz') as archive:
                for path in sorted(run.rglob('*')):
                    if path.is_file() and not path.is_symlink() and path.name!='.cloud.lock':
                        archive.add(path,arcname=str(Path(args.run_id)/path.relative_to(run)),recursive=False)
            print('结果归档（需下载或另存持久化存储）：',destination);return
        if args.stage=='next':
            candidates=[row['instance_id'] for row in snapshot if row['status']=='pending']
            print(json.dumps({'next_instance':candidates[0] if candidates else None,
                 'complete':sum(row['status']=='complete' for row in snapshot),'total':len(items),
                 'needs_attention':[row for row in snapshot if row['status'] not in ('pending','complete')]},ensure_ascii=False,indent=2));return
        if args.stage=='check':
            for item in items:
                adapter.REPO=repo_path(cfg['repos_root'],item)
                adapter.check_repos([item])
                if cfg.get("reviewer_id")!="server_agent" and hasattr(suite,"check_repository"):
                    suite.check_repository(item,adapter.REPO,run/"dataset.jsonl")
                limit=adapter.case_execution_timeout(item) if hasattr(adapter,'case_execution_timeout') else adapter.process_timeout(item)
                print(f"{item.instance_id}：{'仓库映射就绪（远端提交待验证）' if cfg.get('reviewer_id')=='server_agent' else '提交就绪'}，检视外层上限 {limit} 秒；平台命令至少 {limit+60} 秒")
            return
        if args.stage in ('eval','report'):
            if cfg.get('simulation'):raise ValueError('模拟服务结果禁止评分；请配置真实服务并新建批次')
            evaluators.execute(args.stage,suite,items,run,cfg)
            return
        if args.stage == 'review' and not args.instance and cfg.get('reviewer_id', 'lcr') == 'lcr':
            _review_all_lcr_cases(args, cfg, suite, items, snapshot, run)
            return
        if not args.instance:
            p.error('preview/review/recover 必须指定 --instance')
        selected=next((i for i in items if i.instance_id==args.instance),None)
        if selected is None:
            raise ValueError('案例不属于本轮冻结清单')
        adapter.REPO=repo_path(cfg['repos_root'],selected)
        if args.stage=='recover':
            if cfg.get('reviewer_id')=='server_agent' and tool_identity()!=cfg['tool_hashes']:raise ValueError('适配配置已变更，不能恢复旧任务')
            try:adapter.recover_answer(selected,run)
            finally:adapter.write_summary(items,run)
            return
        if args.stage=='review' and (run/'results'/(selected.safe_id+'.json')).exists():
            adapter.validate_answer(selected,run/'results'/(selected.safe_id+'.json'))
            row=next(r for r in snapshot if r['instance_id']==selected.instance_id)
            if row['status']=='complete':
                print('跳过已有完整答卷：',selected.instance_id);return
            if row['status']!='answer_with_item_failures':
                raise ValueError('已有答卷但状态不完整或诊断异常，请先运行 status 检查：'+selected.instance_id)
        if tool_identity()!=cfg['tool_hashes']:
            raise ValueError('本轮检视工具或适配配置已改变，请新建批次')
        adapter.check_repos([selected])
        if cfg.get("reviewer_id")!="server_agent" and hasattr(suite,"check_repository"):
            suite.check_repository(selected,adapter.REPO,run/"dataset.jsonl")
        if args.stage=='review':
            require_model(cfg)
            row=next(r for r in snapshot if r['instance_id']==selected.instance_id)
            auto_retry = row['status'] in ('failed','exited','timed_out','answer_with_item_failures') and cfg.get('max_case_retries',1)>0
            if row['status']!='pending' and not auto_retry and not args.retry:
                raise ValueError('已有未完成执行记录；先检查进程状态，或在确认安全后显式允许重跑')
            required=(adapter.case_execution_timeout(selected) if hasattr(adapter,'case_execution_timeout') else adapter.process_timeout(selected))+60
            if not args.external_timeout_seconds or args.external_timeout_seconds<required:
                raise ValueError(f'平台命令时限不足或未声明：至少 {required} 秒。须同时设置工具 timeout，参数本身不会延长平台时限')
        try:
            adapter._stage_review([selected],run,args.stage=='preview')
        finally:
            if args.stage=='review':
                adapter.write_summary(items,run)
                save(run/'cloud-status.json',states(items,run))


def _review_all_lcr_cases(args, cfg, suite, items, snapshot, run):
    if tool_identity() != cfg['tool_hashes']:
        raise ValueError('本轮检视工具或适配配置已改变，请新建批次')
    require_model(cfg)
    pending = _pending_review_items(args, cfg, items, snapshot, run)
    _check_pending_repositories(pending, cfg, suite, run)
    _check_review_deadline(args, cfg, pending)
    if args.retry:
        _clear_interrupted_workspaces(items, cfg, run)
    try:
        if pending:
            adapter._stage_review(pending, run, False)
    finally:
        adapter.write_summary(items, run)
        save(run / 'cloud-status.json', states(items, run))


def _clear_interrupted_workspaces(items, cfg, run):
    # Caller holds the batch execution lock and has explicitly authorized retry.
    for item in items:
        workspace = run / 'workspaces' / item.safe_id
        if workspace.parent.is_symlink() or workspace.is_symlink():
            raise ValueError('中断工作区不能是链接：' + item.instance_id)
        source = repo_path(cfg['repos_root'], item)
        if not source.exists() and not workspace.exists():
            continue  # A completed case may no longer need its deleted repository.
        _remove_interrupted_workspace(source, workspace)


def _remove_interrupted_workspace(source, workspace):
    import shutil
    import subprocess
    # A killed checkout can leave a locked registration even without a directory.
    # Two force flags allow Git to remove that exact owned worktree registration.
    command = ['git', '-C', str(source), 'worktree', 'remove', '--force', '--force', str(workspace)]
    subprocess.run(command, capture_output=True, timeout=60, check=False)
    if workspace.exists():
        shutil.rmtree(workspace)
        # A half-deleted directory may have lost its .git file, so the first
        # Git removal cannot recognize it. Remove its registration after rmtree.
        subprocess.run(command, capture_output=True, timeout=60, check=False)
    listing = subprocess.run(['git', '-c', 'core.quotePath=false', '-C', str(source),
                              'worktree', 'list', '--porcelain'],
                             capture_output=True, text=True, encoding='utf-8', errors='surrogateescape',
                             timeout=30, check=False)
    registered = {Path(line[9:]).resolve() for line in listing.stdout.splitlines() if line.startswith('worktree ')}
    if listing.returncode or workspace.resolve() in registered:
        raise ValueError('无法清理中断工作区的 Git 记录：' + str(workspace))


def _pending_review_items(args, cfg, items, snapshot, run):
    pending = []
    rows = {row['instance_id']: row for row in snapshot}
    for item in items:
        status = rows[item.instance_id]['status']
        if status == 'complete':
            adapter.validate_answer(item, run / 'results' / (item.safe_id + '.json'))
            print('跳过已有完整答卷：', item.instance_id)
        elif status == 'answer_with_item_failures' and cfg.get('max_case_retries', 1) > 0:
            pending.append(item)
            print('答卷的 LCR 会话有失败项，将保留旧答卷并自动重跑：', item.instance_id)
        elif status == 'recoverable_check_required':
            _recover_or_queue(item, args, cfg, run, pending)
        elif _can_start_again(status, args, cfg):
            pending.append(item)
        else:
            raise ValueError('案例已有未完成执行记录，需先检查进程状态；确认安全后可显式允许重跑：' + item.instance_id)
    return pending


def _recover_or_queue(item, args, cfg, run, pending):
    try:
        adapter.recover_answer(item, run)
        print('从已有输出恢复完整答卷，未重新调用模型：', item.instance_id)
    except (ValueError, OSError) as error:
        print('已有输出无法恢复，将按失败案例策略处理：', item.instance_id, error, file=sys.stderr)
        if 'budget_exceeded' in str(error) and not args.retry:
            raise ValueError('Token 预算耗尽，不自动续跑；请明确确认重跑或新建批次调整预算') from error
        if cfg.get('max_case_retries', 1) > 0 or args.retry:
            pending.append(item)
        else:
            raise


def _can_start_again(status, args, cfg):
    retryable = status in ('failed', 'exited', 'timed_out') and cfg.get('max_case_retries', 1) > 0
    return status == 'pending' or retryable or args.retry


def _check_pending_repositories(pending, cfg, suite, run):
    for item in pending:
        adapter.REPO = repo_path(cfg['repos_root'], item)
        adapter.check_repos([item])
        if hasattr(suite, 'check_repository'):
            suite.check_repository(item, adapter.REPO, run / 'dataset.jsonl')


def _check_review_deadline(args, cfg, pending):
    if not args.external_timeout_seconds:
        raise ValueError('平台命令时限未声明')
    limits = []
    for item in pending:
        adapter.REPO = repo_path(cfg['repos_root'], item)
        limit = adapter.case_execution_timeout(item) if hasattr(adapter, 'case_execution_timeout') else adapter.process_timeout(item)
        limits.append(limit)
    jobs = max(1, getattr(adapter, 'CASE_JOBS', 1))
    required = sum(max(limits[offset:offset + jobs]) for offset in range(0, len(limits), jobs)) + 60 * len(limits)
    required = max(required, 60)
    if args.external_timeout_seconds < required:
        raise ValueError(f'平台命令时限不足：至少 {required} 秒')


if __name__=='__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('已中断，已写入的结果保留；运行 status 查看状态。',file=sys.stderr)
        sys.exit(130)
    except (ValueError,OSError) as exc:
        print('停止：'+adapter.redact(str(exc)),file=sys.stderr)
        sys.exit(1)
