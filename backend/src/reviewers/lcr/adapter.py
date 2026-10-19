"""LCR review adapter. Does not load benchmark references or select a scorer."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED, CancelledError
import threading
from datetime import datetime, timezone
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys

from runner.storage import ROOT, save, safe_json, validate_answer
from runner.process import capture_process
from .options import freeze_options, command_options, case_timeout
from .session_logs import collect_session
from .logs import parse_lcr_output, redact, write_summary

REPO = ROOT / "repos"
CASE_JOBS = 2
FILE_CONCURRENCY = 1
REPOS_ROOT = ROOT / "repos"
WORKSPACE_LOCK = threading.Lock()
WORKER = threading.local()

LCR_TIMEOUT_MINUTES = 10
MAX_CASE_RETRIES = 1
LCR_OPTIONS = {}
PROCESS_TIMEOUT_POLICY = "changed_files * 600 + 120 seconds"


def git(*args):
    repo = getattr(WORKER, "repo", REPO)
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, timeout=60, check=False)


def check_repos(instances):
    if not (REPO / ".git").exists():
        raise SystemExit("缺少包含 .git 的案例仓库")
    for item in instances:
        for commit in (item.base_commit, item.head_commit):
            if git("cat-file", "-e", f"{commit}^{{commit}}").returncode:
                raise SystemExit(f"本地缺少提交 {commit}，案例 {item.instance_id}")
        if git("merge-base", item.base_commit, item.head_commit).returncode:
            raise SystemExit(f"历史不完整，案例 {item.instance_id}")


def process_timeout(item):
    ancestor = git("merge-base", item.base_commit, item.head_commit)
    if ancestor.returncode:
        raise ValueError("无法计算案例进程时限：merge-base 失败")
    diff = git("diff", "--name-only", "-z", ancestor.stdout.strip(), item.head_commit, "--")
    if diff.returncode:
        raise ValueError("无法计算案例进程时限：文件清单读取失败")
    # Count every changed file conservatively, including files LCR may exclude.
    count = len([path for path in diff.stdout.split("\0") if path])
    return case_timeout(LCR_OPTIONS, count, LCR_TIMEOUT_MINUTES)


def case_execution_timeout(item):
    backoff = sum(min(5 * attempt, 15) for attempt in range(1, MAX_CASE_RETRIES + 1))
    return process_timeout(item) * (MAX_CASE_RETRIES + 1) + backoff


def _check_budget(result):
    if result.get('status') == 'budget_exceeded':
        raise ValueError('budget_exceeded：Token 预算耗尽；部分结果已保留，不自动重试')


def convert(item, result):
    state = result.get("status")
    _check_budget(result)
    if state not in ("success", "completed"):
        raise ValueError(f"状态 {state!r}；请检查原始结果，不将部分成功计入完整答卷")
    if any(isinstance(w, dict) and w.get("type") == "subtask_error" for w in (result.get("warnings") or [])):
        raise ValueError("结果含逐文件失败，不作为完整答卷")
    comments = result.get("comments")
    if not isinstance(comments, list):
        raise ValueError("缺少 comments 数组")
    summary = result.get("summary") or {}
    if not isinstance(summary, dict):
        raise ValueError("summary 不是对象")
    if "comments" in summary and summary["comments"] != len(comments):
        raise ValueError("summary.comments 与实际意见数不一致")
    adapted = [_convert_comment(index, comment) for index, comment in enumerate(comments, 1)]
    return {"instance_id": item.instance_id, "repo": item.repo,
            "base_commit": item.base_commit, "head_commit": item.head_commit,
            "reviewer": "lcr", "ocr_exit_code": 0,
            "review": {"status": "complete", "comments": adapted,
                       "summary": summary,
                       "tool_calls": result.get("tool_calls"),
                       "session_id": result.get("session_id"),
                       "llm": {"model": os.getenv("LCR_LLM_MODEL", "请参阅 run-config.json")}},
            "conversion": "LCR filePath/startLine/endLine/description+fixSuggestion -> official reviewer fields; raw JSON retained"}


def _valid_location(path, line, end):
    return (isinstance(path, str) and bool(path) and not path.startswith(("/", "\\"))
            and ":" not in path and ".." not in Path(path).parts
            and type(line) is int and line >= 1 and type(end) is int and end >= line)


def _convert_comment(index, comment):
    if not isinstance(comment, dict):
        raise ValueError(f"第 {index} 条意见不是对象")
    path = comment.get("filePath")
    line = comment.get("startLine")
    end = comment.get("endLine", line)
    description = comment.get("description")
    suggestion = comment.get("fixSuggestion")
    if not _valid_location(path, line, end) or not isinstance(description, str) or not description.strip():
        raise ValueError(f"第 {index} 条意见的位置或描述无效")
    if suggestion is not None and not isinstance(suggestion, str):
        raise ValueError(f"第 {index} 条意见的 fixSuggestion 无效")
    return {"path": path.replace("\\", "/"), "start_line": line,
                    "end_line": end,
                    "content": description.strip() + (("\n" + suggestion.strip()) if suggestion and suggestion.strip() else ""),
                    "source_issue_key": comment.get("issueKey")}


def recover_answer(item, run):
    """Adapt a saved successful invocation without launching LCR or a model."""
    destination = run / "results" / (item.safe_id + ".json")
    if destination.exists():
        validate_answer(item, destination)
        print("跳过已有完整答卷：", item.instance_id)
        return
    folder = run / "raw"
    stdout_path = folder / (item.safe_id + ".stdout.json")
    execution_path = folder / (item.safe_id + ".execution.json")
    execution = safe_json(execution_path.read_text(encoding="utf-8"), "执行记录")
    if execution.get("status") != "exited" or execution.get("exit_code") != 0:
        raise ValueError("原执行未正常成功退出，不能恢复为完整答卷")
    command = execution.get("command_without_credentials")
    if not isinstance(command, list) or "--preview" in command:
        raise ValueError("执行记录不是正式检视命令")
    for flag, expected in (("--from", item.base_commit), ("--to", item.head_commit), ("--format", "json")):
        if (command.count(flag) != 1 or command.index(flag) + 1 >= len(command)
                or command[command.index(flag) + 1] != expected):
            raise ValueError(f"执行记录 {flag} 与案例或正式 JSON 检视不符")
    raw = parse_lcr_output(stdout_path.read_text(encoding="utf-8-sig"))
    _check_budget(raw)
    failures = case_session_failures(item, run, raw)
    if failures:
        raise ValueError("原始 LCR 会话含失败条目，不能恢复成完整答卷")
    answer = convert(item, raw)
    answer["duration_seconds"] = execution.get("elapsed_seconds")
    answer["recovery"] = {
        "mode": "adapt_saved_success_no_model_calls",
        "stdout_sha256": hashlib.sha256(stdout_path.read_bytes()).hexdigest(),
        "execution_sha256": hashlib.sha256(execution_path.read_bytes()).hexdigest(),
    }
    save(destination, answer)
    print("已从原始文件恢复完整答卷（未调用模型）：", item.instance_id)


def stage_recover(instances, run):
    for item in instances:
        try:
            recover_answer(item, run)
        except (ValueError, OSError) as error:
            raise SystemExit(f"恢复失败：{item.instance_id}；{error}；原始文件保留，不自动重跑。")


def cancellation_requested():
    stop = getattr(WORKER, "stop", None)
    marker = getattr(WORKER, "stop_file", None)
    return (stop is not None and stop.is_set()) or (marker is not None and marker.exists())


def _lcr_executable():
    executable = shutil.which("lcr.cmd") if os.name == "nt" else shutil.which("lcr")
    if not executable:
        raise ValueError("找不到 LCR 启动器；请在同一 PowerShell 窗口运行 Get-Command lcr 检查 PATH")
    return executable


def _lcr_review_command(executable, repo, item, preview, diagnostic, resume_session_id):
    command = [executable, "review", "--repo", str(repo), "--from", item.base_commit,
               "--to", item.head_commit, "--format", "text" if diagnostic else "json",
               "--audience", "human",
               "--concurrency", str(FILE_CONCURRENCY), "--timeout", str(LCR_TIMEOUT_MINUTES)]
    command.extend(command_options(LCR_OPTIONS or freeze_options()))
    if preview:
        command.append("--preview")
    else:
        command.append("--keep-session")
        if resume_session_id:
            command.extend(["--resume", resume_session_id])
            print("续跑 LCR 会话，复用已完成文件：", resume_session_id, flush=True)
    return command


def _platform_command(command, executable):
    if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", subprocess.list2cmdline(command)]
    return command


def _raise_lcr_failure(completed, folder):
    detail = (completed.stderr or completed.stdout or "").strip()
    detail = redact(detail[-4000:]) if detail else ""
    suffix = ("；错误摘要：" + detail) if detail else ""
    error = ValueError(f"LCR 退出码 {completed.returncode}{suffix}；参见 {folder}")
    error.resume_session_id = _resume_id_from_output(completed.stdout, completed.stderr)
    raise error


def _interpret_lcr_result(completed, preview, diagnostic):
    if diagnostic:
        return {"status": "diagnostic_exited", "note": "诊断输出不计入正式答卷"}
    if preview:
        output = completed.stdout.strip()
        if output.startswith("Preview:") and "file(s) changed" in output:
            return {"status": "preview_completed", "preview_text": output}
    return parse_lcr_output(completed.stdout)


def run_lcr(item, preview, run, diagnostic=False, resume_session_id=None):
    repo = getattr(WORKER, "repo", REPO)
    executable = _lcr_executable()
    command = _platform_command(_lcr_review_command(executable, repo, item, preview, diagnostic, resume_session_id), executable)
    print("运行：", " ".join(command), flush=True)
    folder = run / ("diagnostic" if diagnostic else "preview" if preview else "raw")
    outer_timeout = 120 if preview else process_timeout(item)
    print(f"本案例外层进程上限：{outer_timeout // 60} 分钟（提前结束即返回）", flush=True)
    completed = capture_process(command, folder, item.safe_id,
                                timeout=outer_timeout, cwd=repo, case_jobs=CASE_JOBS,
                                cancellation_requested=cancellation_requested, redact=redact,
                                stdout_suffix=".stdout.txt" if diagnostic else ".stdout.json")
    if completed.returncode:
        _raise_lcr_failure(completed, folder)
    return _interpret_lcr_result(completed, preview, diagnostic)


def stage_review(instances, run, preview):
    try:
        _stage_review(instances, run, preview)
    finally:
        if not preview:
            try:
                write_summary(instances, run)
                print("运行汇总：", run / "run-summary.zh-CN.md", flush=True)
            except Exception as error:
                print("运行汇总生成失败，原始日志保留：", redact(error), file=sys.stderr)


def review_case(item, run, preview, stop):
    WORKER.stop = stop
    WORKER.stop_file = run / "stop-request.json"
    retries = []
    try:
        if REPOS_ROOT is not None:
            WORKER.repo = run / "workspaces" / item.safe_id
        if cancellation_requested():
            return item.instance_id
        out = run / "results" / (item.safe_id + ".json")
        if _skip_or_archive_existing_answer(item, run, out, preview):
            return None
        _prepare_case_workspace(item, run)
        if cancellation_requested():
            raise CancelledError("工作区准备完成后收到停止请求")
        resume_session_id = None if preview else _initial_resume_session(item, run)
        for attempt in range(1, (1 if preview else MAX_CASE_RETRIES + 1) + 1):
            raw = None
            try:
                raw = _run_case_attempt(item, run, out, preview, attempt, retries, resume_session_id)
                return None
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                raw = getattr(error, "raw_output", raw)
                can_retry = attempt <= MAX_CASE_RETRIES and retryable_case_error(error, raw)
                if not can_retry:
                    raise
                resume_session_id = _retry_session(item, run, error, raw, resume_session_id)
                retries.append({"attempt": attempt, "error": redact(f"{type(error).__name__}: {error}")})
                print(f"案例失败，将在短暂等待后自动重试（{attempt}/{MAX_CASE_RETRIES}）：",
                      item.instance_id, redact(error), file=sys.stderr, flush=True)
                if stop.wait(min(5 * attempt, 15)):
                    raise CancelledError("自动重试等待期间收到停止请求")
        return None
    except (ValueError, OSError, subprocess.TimeoutExpired, CancelledError) as error:
        print("案例未完成：", item.instance_id, type(error).__name__, error, file=sys.stderr)
        save(run / ("preview" if preview else "raw") / (item.safe_id + ".outcome.json"),
             {"error": redact(f"{type(error).__name__}: {error}"),
              "attempt_count": len(retries) + 1, "retry_history": retries})
        return item.instance_id
    finally:
        try:
            _cleanup_case_workspace(item)
        finally:
            WORKER.__dict__.clear()


def _skip_or_archive_existing_answer(item, run, out, preview):
    if preview or not out.exists():
        return False
    existing = validate_answer(item, out)
    if not answer_session_failures(item, run, existing):
        print("跳过已有完整答卷：", item.instance_id)
        return True
    archive = run / "raw" / "rejected-answers"
    archive.mkdir(parents=True, exist_ok=True)
    archived = archive / f"{item.safe_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
    shutil.copy2(out, archived)
    out.unlink()
    print("已有答卷对应的 LCR 会话含失败项，保留旧答卷并自动重跑：", item.instance_id)
    return False


def _run_case_attempt(item, run, out, preview, attempt, retries, resume_session_id=None):
    raw = run_lcr(item, preview, run, resume_session_id=resume_session_id)
    try:
        if preview:
            print("预览完成：", item.instance_id, "状态", raw.get("status"))
            return raw
        _check_budget(raw)
        failures = case_session_failures(item, run, raw)
        if failures:
            raise ValueError("review_item_failed: " + json.dumps(failures, ensure_ascii=False))
        answer = convert(item, raw)
        execution = json.loads((run / "raw" / (item.safe_id + ".execution.json")).read_text(encoding="utf-8"))
        answer["duration_seconds"] = execution["elapsed_seconds"]
        save(out, answer)
        _request_checkpoint_sync()
        if retries:
            save(run / "raw" / (item.safe_id + ".outcome.json"),
                 {"status": "recovered_after_retry", "attempt_count": attempt, "retry_history": retries})
        print("检视完成：", item.instance_id, f"（自动重试 {len(retries)} 次）" if retries else "")
        return raw
    except (ValueError, OSError) as error:
        error.raw_output = raw
        raise


def _request_checkpoint_sync():
    if os.environ.get('BENCH_RUN_STORAGE_MODE', 'local').lower() != 'cos':
        return
    from deployment.run_storage import request_run_sync
    from runner.runtime import data_root
    try:
        request_run_sync(data_root(ROOT))
    except OSError as error:
        # A backup scheduling error must never turn a complete answer into failure.
        print('答卷已保存，但 COS 同步请求写入失败：', type(error).__name__, file=sys.stderr)


def _resume_id_from_result(raw):
    if not isinstance(raw, dict):
        return None
    value = raw.get("session_id")
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F-]{36}", value) else None


def _resume_id_from_output(stdout, stderr):
    try:
        value = json.loads(stdout).get("session_id")
        if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F-]{36}", value):
            return value
    except (ValueError, AttributeError, TypeError):
        pass
    for output in (stderr, stdout):
        match = re.search(r"(?:Session:\s*|--resume\s+)([0-9a-fA-F-]{36})", output or "")
        if match:
            return match.group(1)
    return None


def _resume_id_from_saved_logs(run, safe_id):
    folder = run / "raw"
    try:
        stderr = (folder / (safe_id + ".stderr.txt")).read_text(encoding="utf-8", errors="replace")
        stdout = (folder / (safe_id + ".stdout.json")).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return _resume_id_from_output(stdout, stderr)


def _initial_resume_session(item, run):
    session_id = _resume_id_from_saved_logs(run, item.safe_id)
    if not session_id:
        path = run / 'raw' / (item.safe_id + '.execution.json')
        if path.exists():
            try:
                execution = json.loads(path.read_text(encoding='utf-8'))
                matched = collect_session(item, run, execution, None, redact)
                session_id = matched.get('session_id') if matched.get('match') == 'matched' else None
            except (OSError, ValueError):
                pass
    return session_id if session_id and _session_has_completed_items(item, run, session_id) else None


def _retry_session(item, run, error, raw, previous_session_id):
    # A rejected resume must not select the same ID from the old logs again.
    if "resume session" in str(error).lower():
        print("LCR 拒绝续跑会话；清除会话 ID，下次重试重新检视案例。", flush=True)
        return None
    session_id = (_resume_id_from_result(raw)
                  or getattr(error, "resume_session_id", None)
                  or _resume_id_from_saved_logs(run, item.safe_id)
                  or previous_session_id)
    if not session_id:
        return None
    if _session_has_completed_items(item, run, session_id):
        return session_id
    print("没有确认到可复用的已完成文件；下次重试重新检视案例。", flush=True)
    return None


def _session_has_completed_items(item, run, session_id):
    try:
        execution_path = run / "raw" / (item.safe_id + ".execution.json")
        execution = json.loads(execution_path.read_text(encoding="utf-8"))
        evidence = collect_session(item, run, execution, session_id, redact)
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    return evidence.get("match") == "matched" and evidence.get("completed_review_items", 0) > 0


def _create_case_workspace(source, workspace, item):
    if workspace.exists():
        raise ValueError(f"案例工作区已存在，先检查上次任务是否结束：{workspace}")
    # Match offline repository preparation: LFS media are not bundled, and
    # checkout must not contact an external LFS endpoint.
    command = ["git", "-C", str(source), "-c", "filter.lfs.process=",
               "-c", "filter.lfs.smudge=", "-c", "filter.lfs.required=false",
               "worktree", "add", "--detach", str(workspace), item.head_commit]
    try:
        result = subprocess.run(command, env=dict(os.environ, GIT_LFS_SKIP_SMUDGE="1"),
                                capture_output=True, text=True, timeout=180, check=False)
        if result.returncode:
            raise ValueError(f"创建案例独立工作区失败：{item.instance_id}；{(result.stderr or result.stdout).strip()}")
    except BaseException:
        subprocess.run(["git", "-C", str(source), "worktree", "remove", "--force", str(workspace)],
                       capture_output=True, text=True, timeout=60, check=False)
        raise


def _check_case_source(source, item):
    if not (source / ".git").is_dir():
        raise ValueError(f"缺少案例仓库：{source}")
    commands = [("cat-file", "-e", commit + "^{commit}")
                for commit in (item.base_commit, item.head_commit)]
    commands.append(("merge-base", item.base_commit, item.head_commit))
    for command in commands:
        result = subprocess.run(["git", "-C", str(source), *command],
                                capture_output=True, timeout=30, check=False)
        if result.returncode:
            raise ValueError(f"案例提交缺失或历史不完整：{item.instance_id}")


def _prepare_case_workspace(item, run):
    if REPOS_ROOT is None:
        return
    source = Path(REPOS_ROOT) / item.repo.replace("/", "__")
    workspace = run / "workspaces" / item.safe_id
    workspace.parent.mkdir(parents=True, exist_ok=True)
    with WORKSPACE_LOCK:
        if cancellation_requested():
            raise CancelledError("准备工作区前收到停止请求")
        print(f"正在准备案例工作区：{item.instance_id}", flush=True)
        _check_case_source(source, item)
        _create_case_workspace(source, workspace, item)
        WORKER.workspace_source = source
        WORKER.workspace = workspace
    print(f"案例工作区已就绪：{item.instance_id}", flush=True)


def _cleanup_case_workspace(item):
    workspace = getattr(WORKER, "workspace", None)
    if workspace is None:
        return
    # LCR sessions live in ~/.leancodereview; answers and logs live under run.
    # Keep the deterministic workspace path when recreating it for --resume.
    try:
        with WORKSPACE_LOCK:
            result = subprocess.run(["git", "-C", str(WORKER.workspace_source), "worktree",
                                     "remove", "--force", str(workspace)],
                                    capture_output=True, text=True, timeout=60, check=False)
        if result.returncode:
            print(f"独立工作区清理失败：{workspace}；{(result.stderr or result.stdout).strip()}", file=sys.stderr, flush=True)
        else:
            print(f"案例工作区已清理：{item.instance_id}", flush=True)
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"独立工作区清理失败：{workspace}；{redact(error)}", file=sys.stderr, flush=True)


def _wait_review_futures(futures, stop, interrupted):
    failures = []
    announced = False
    pending = set(futures)
    while pending:
        if stop.is_set():
            if interrupted.is_set() and not announced:
                print("已收到取消请求，正在终止本轮 LCR 子进程并保存日志；请勿关闭窗口。", flush=True)
                announced = True
            for future in pending:
                future.cancel()
            pending = {future for future in pending if not future.cancelled()}
            if not pending:
                break
        # Windows indefinite condition waits can delay Python signal handling.
        # Return to the main thread regularly, including when LCR is silent.
        done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
        for future in done:
            if future.cancelled():
                continue
            failure = future.result()
            if failure:
                failures.append(failure)
    return failures


def _stage_review(instances, run, preview):
    if (run / "stop-request.json").exists():
        raise SystemExit("本轮已有停止请求，不再启动任务。核对日志后使用新轮次。")
    if REPOS_ROOT is None:
        check_repos(instances)
    if not instances:
        return
    stop = threading.Event()
    jobs = min(CASE_JOBS, len(instances))
    print(f"案例并发：{jobs}；每案例文件并发：1；每案例独立 Git 工作树；日志按案例独立保存", flush=True)
    failures = []
    pool = ThreadPoolExecutor(max_workers=jobs)
    futures = []
    interrupted = threading.Event()
    previous_handlers = {}

    def request_interrupt(signum, frame):
        # Never raise during process-tree cleanup, even on repeated Ctrl+C.
        interrupted.set()
        stop.set()

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, getattr(signal, "SIGBREAK", None)):
            if signum is not None:
                previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, request_interrupt)
    try:
        futures = [pool.submit(review_case, item, run, preview, stop) for item in instances]
        failures = _wait_review_futures(futures, stop, interrupted)
    finally:
        # Ctrl+C signals running workers, whose capture_process cleans its own tree.
        stop.set()
        for future in futures:
            future.cancel()
        try:
            pool.shutdown(wait=True, cancel_futures=True)
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
    if interrupted.is_set():
        raise KeyboardInterrupt
    if failures:
        raise SystemExit("本阶段未完成；请查看运行汇总，失败案例：" + "、".join(failures))


CAPABILITIES = frozenset({"git_range", "findings"})

def tool_identity():
    runtime_path = ROOT/'local-runtime.json'
    if runtime_path.exists():
        runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
        if runtime.get('platform') != platform.system() or runtime.get('architecture') != platform.machine():
            raise ValueError('本地 runtime 与当前平台不一致')
        binary_name = runtime.get('binary')
        expected = {('Darwin', 'arm64'): 'lcr-darwin-arm64',
                    ('Windows', 'AMD64'): 'lcr-win32-x64.exe',
                    ('Windows', 'x86_64'): 'lcr-win32-x64.exe'}.get((platform.system(), platform.machine()))
        if expected is None or binary_name != expected:
            raise ValueError('本地 runtime 二进制无效')
        launcher = 'lcr.cmd' if platform.system() == 'Windows' else 'lcr'
        if platform.system() == 'Windows':
            package_root = ROOT/'.runtime/lcr/node_modules/@LV01.30/leancode-code-review-cli'
            paths = [package_root/'dist'/binary_name, ROOT/'bin'/launcher, runtime_path,
                     package_root/'package.json', package_root/'bin/lcr.js', package_root/'bin/version-check.js',
                     ROOT/'backend/src/reviewers/lcr/windows-runtime.json']
            rules_root = package_root/'rules'
        else:
            paths = [ROOT/'lcr/dist'/binary_name, ROOT/'bin'/launcher, runtime_path, ROOT/'lcr/package.json']
            rules_root = ROOT/'lcr/rules'
    else:
        binary = ROOT / 'lcr/dist/lcr-linux-x64'
        paths = [binary, ROOT/'lcr/bin/lcr.js', ROOT/'lcr/bin/version-check.js', ROOT/'lcr/package.json']
    if runtime_path.exists() and platform.system() == 'Windows':
        paths += sorted(p for p in rules_root.rglob('*') if p.is_file())
    else:
        paths += sorted(p for p in (ROOT/'lcr/rules').rglob('*') if p.is_file())
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}



def retryable_case_error(error, raw=None):
    if (isinstance(raw, dict) and raw.get("status") == "budget_exceeded") or "budget_exceeded" in str(error):
        return False
    if isinstance(error, subprocess.TimeoutExpired):
        return True
    text = "\n".join([str(error), *_structured_error_details(raw)]).lower()
    if (re.search(r"\b(?:401|403)\b", text)
            or any(marker in text for marker in ("unauthorized", "forbidden", "not found", "no such file",
                "file_read failed", "git returned", "invalid configuration", "missing required", "permission denied"))):
        return False
    if isinstance(raw, dict) and raw.get("status") in ("failed", "error", "completed_with_errors", "completed_with_warnings"):
        return True
    if isinstance(raw, dict):
        # The CLI returned structured output, but adaptation rejected it. A
        # fresh review can correct malformed or incomplete model output.
        return True
    return bool(re.search(r"\b(?:504|502|503|429)\b", text)) or any(marker in text for marker in (
        "gateway timeout", "timed out", "timeout", "deadline exceeded", "connection reset",
        "connection refused", "context canceled", "review_item_failed", "llm_error",
        "completed_with_errors", "subtask_error", "lcr 退出码", "未返回唯一、完整的结果 json",
        "lcr 输出顶层不是对象", "comments 数组", "意见的位置或描述无效"))


def _structured_error_details(raw):
    if not isinstance(raw, dict):
        return []
    details = []
    warnings = raw.get("warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            if isinstance(warning, dict):
                details.extend(str(warning[key]) for key in ("message", "error", "detail") if warning.get(key))
    for key in ("error", "errors", "failure", "failures"):
        value = raw.get(key)
        if isinstance(value, str):
            details.append(value)
        elif isinstance(value, list):
            details.extend(json.dumps(entry, ensure_ascii=False) for entry in value)
    return details


def _session_errors(item, run, execution, session_id):
    evidence = collect_session(item, run, execution, session_id, redact)
    if evidence.get('match') != 'matched':
        return []
    return evidence.get('errors') or []


def case_session_failures(item, run, raw=None):
    folder = run / 'raw'
    try:
        execution = safe_json((folder / (item.safe_id + '.execution.json')).read_text(encoding='utf-8'), '执行记录')
        if raw is None:
            raw = parse_lcr_output((folder / (item.safe_id + '.stdout.json')).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return []
    return _session_errors(item, run, execution, raw.get('session_id'))


def answer_session_failures(item, run, answer):
    review = answer.get('review') if isinstance(answer, dict) else None
    if not isinstance(review, dict) or answer.get('reviewer') != 'lcr':
        return []
    session_id = review.get('session_id')
    stored = saved_session_failures(item, run, session_id)
    if stored:
        return stored
    try:
        execution = safe_json((run / 'raw' / (item.safe_id + '.execution.json')).read_text(encoding='utf-8'), '执行记录')
        raw = parse_lcr_output((run / 'raw' / (item.safe_id + '.stdout.json')).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return []
    if session_id and raw.get('session_id') != session_id:
        return []
    return _session_errors(item, run, execution, session_id or raw.get('session_id'))


def saved_session_failures(item, run, session_id=None):
    # Historical diagnostics let a resumed run identify hidden per-item errors
    # without rescanning the full session directory on every dashboard refresh.
    path = run / 'diagnostics' / (item.safe_id + '.json')
    try:
        data = safe_json(path.read_text(encoding='utf-8'), '案例诊断记录')
    except (OSError, ValueError):
        return []
    evidence = data.get('session_evidence') or {}
    if session_id and evidence.get('session_id') != session_id:
        return []
    return evidence.get('errors') or []


def configure(cfg, run):
    global CASE_JOBS, LCR_TIMEOUT_MINUTES, FILE_CONCURRENCY, REPOS_ROOT, MAX_CASE_RETRIES, LCR_OPTIONS
    LCR_OPTIONS = cfg.get("lcr_options") or freeze_options()
    os.environ["LCR_LLM_STREAM"] = str(LCR_OPTIONS["stream"]).lower()
    case_jobs = cfg.get('case_jobs', 1)
    if type(case_jobs) is not int or not 1 <= case_jobs <= 16:
        raise ValueError('案例并发必须为 1–16 的整数')
    CASE_JOBS = case_jobs
    retries = cfg.get('max_case_retries', 1)
    if type(retries) is not int or not 0 <= retries <= 2:
        raise ValueError('失败案例自动重试次数必须为 0–2')
    MAX_CASE_RETRIES = retries
    FILE_CONCURRENCY = 1
    REPOS_ROOT = Path(cfg.get('repos_root', ROOT/'repos'))
    LCR_TIMEOUT_MINUTES = cfg['timeout_minutes']
    os.environ['PATH'] = str(ROOT/'bin') + os.pathsep + os.environ.get('PATH','')
    os.environ['LCR_DIAGNOSTICS_DIR'] = str(run/'diagnostics')



def require_model(cfg):
    for key in ('LCR_LLM_BASE_URL','LCR_LLM_TOKEN'):
        if not os.getenv(key):
            raise ValueError('请在平台环境变量中配置 '+key+'，不要将令牌写入命令或日志')
    endpoint_hash=cfg.get('review_endpoint_sha256')
    if endpoint_hash and hashlib.sha256(os.environ['LCR_LLM_BASE_URL'].encode()).hexdigest()!=endpoint_hash:
        raise ValueError('本轮检视接口已改变，请使用新批次，不混用接口')
    if os.getenv('LCR_LLM_MODEL') != cfg['review_model']:
        raise ValueError('请在平台环境变量中设置与本轮 --model 一致的 LCR_LLM_MODEL')
