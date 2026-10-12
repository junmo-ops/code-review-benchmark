"""Offline retry rehearsal using the real adapter and a simulated CLI process."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "backend/src"))
sys.path.insert(0, str(ROOT))

SCENARIOS = {
    "gateway": ("504 后重新运行成功", [False, False], True),
    "timeout": ("外层进程超时，终止后重试成功", [False, False], True),
    "empty-session": ("会话没有已完成文件，重新运行成功", [False, False], True),
    "partial-session": ("复用已完成文件，续跑剩余文件成功", [False, True], True),
    "resume-rejected": ("续跑被拒绝，清除会话后重新运行成功", [False, True, False], True),
    "exhausted": ("持续 504，重试两次后停止", [False, False, False], False),
    "unauthorized": ("401 认证失败，直接停止", [False], False),
    "recovered-error": ("历史报错已恢复，正常保存结果", [False], True),
    "invalid-json": ("输出 JSON 无效，重新运行成功", [False, False], True),
}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def flag(args, name):
    return args[args.index(name) + 1] if name in args else None


def session_events(folder, args, kinds):
    session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(folder)))
    path = folder / "sessions" / (session_id + ".jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    if not path.exists():
        records.append({"type": "session_start", "sessionId": session_id,
                        "cwd": flag(args, "--repo"), "diffFrom": flag(args, "--from"),
                        "diffTo": flag(args, "--to")})
    for kind, file in kinds:
        records.append({"type": kind, "sessionId": session_id, "filePath": file,
                        "fingerprint": file,
                        "error": "504 Gateway Timeout" if kind in {"llm_error", "review_item_failed"} else None})
    with path.open("a", encoding="utf-8") as stream:
        for record in records:
            record["timestamp"] = datetime.now(timezone.utc).isoformat()
            stream.write(json.dumps(record) + "\n")
    return session_id


def cli_failure(message, session_id=None):
    if session_id:
        print(f"[lcr] Session: {session_id} (retry with: --resume {session_id})", file=sys.stderr)
    print("[lcr] Error: " + message, file=sys.stderr, flush=True)
    return 1


def partial_output(folder, args):
    session_id = session_events(folder, args, [("review_item_done", "done.ts"),
                                              ("review_item_failed", "pending.ts")])
    print(json.dumps({"status": "completed_with_errors", "session_id": session_id,
                      "comments": [], "summary": {"comments": 0},
                      "warnings": [{"type": "subtask_error", "message": "504 Gateway Timeout"}]}))
    return 0


def success_output(folder, args, recovered=False):
    session_id = None
    if flag(args, "--resume"):
        session_id = session_events(folder, args, [("review_item_reused", "done.ts"),
                                                  ("review_item_done", "pending.ts")])
    elif recovered:
        session_id = session_events(folder, args, [("llm_error", "pending.ts"),
                                                  ("review_item_failed", "pending.ts"),
                                                  ("review_item_done", "pending.ts")])
    print(json.dumps({"status": "success", "comments": [], "summary": {"comments": 0},
                      "session_id": session_id, "simulation": True}))
    return 0


def record_invocation(folder, args):
    state_path = folder / "invocations.json"
    calls = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else []
    calls.append({"attempt": len(calls) + 1, "resume": flag(args, "--resume")})
    save(state_path, calls)
    return len(calls)


def simulate_failure(scenario, attempt):
    if scenario == "unauthorized":
        return cli_failure("401 Unauthorized")
    if scenario == "exhausted" or (scenario == "gateway" and attempt == 1):
        return cli_failure("504 Gateway Timeout")
    if scenario == "timeout" and attempt == 1:
        print("[lcr] 模拟进程持续等待，等待外层终止", flush=True)
        time.sleep(60)
        return 1
    return None


def simulate_cli(folder, args):
    """Only deterministic local output; never launch LCR or access a provider."""
    scenario = folder.name
    attempt = record_invocation(folder, args)
    failure = simulate_failure(scenario, attempt)
    if failure is not None:
        return failure
    if scenario == "empty-session" and attempt == 1:
        session_id = session_events(folder, args, [("review_item_failed", "pending.ts")])
        return cli_failure("review failed: all 1 file review(s) failed", session_id)
    if scenario in {"partial-session", "resume-rejected"} and attempt == 1:
        return partial_output(folder, args)
    if scenario == "resume-rejected" and attempt == 2:
        return cli_failure(f'resume session "{flag(args, "--resume")}" has no completed review items')
    if scenario == "invalid-json" and attempt == 1:
        print('{"status":')
        return 0
    return success_output(folder, args, recovered=scenario == "recovered-error")


def run_scenario(name, folder):
    from reviewers.lcr import adapter
    from reviewers.lcr.session_logs import collect_session

    folder.mkdir(parents=True, exist_ok=True)
    repo = folder / "workspace"
    repo.mkdir()
    item = SimpleNamespace(instance_id="rehearsal@" + name, safe_id=name,
                           repo="rehearsal/local", base_commit="a" * 40, head_commit="b" * 40)
    save(folder / "run-config.json", {"simulation": True, "rehearsal": True, "max_case_retries": 2})

    def command(cli_args, executable):
        return [sys.executable, str(Path(__file__).resolve()), "--simulate-cli", str(folder), *cli_args[1:]]

    def evidence(*args):
        return collect_session(*args, session_root=folder / "sessions")

    replacements = {"REPO": repo, "REPOS_ROOT": None, "MAX_CASE_RETRIES": 2, "CASE_JOBS": 1,
                    "_lcr_executable": lambda: sys.executable, "_platform_command": command,
                    "process_timeout": lambda item: 1 if name == "timeout" else 10,
                    "collect_session": evidence}
    with ExitStack() as stack:
        for key, value in replacements.items():
            stack.enter_context(patch.object(adapter, key, value))
        result = adapter.review_case(item, folder, False, threading.Event())
    calls = json.loads((folder / "invocations.json").read_text(encoding="utf-8"))
    answer_path = folder / "results" / (name + ".json")
    succeeded = result is None and answer_path.exists()
    if answer_path.exists():
        answer = json.loads(answer_path.read_text(encoding="utf-8"))
        answer.update(simulation=True, rehearsal=True)
        save(answer_path, answer)
    actual_resume = [bool(call["resume"]) for call in calls]
    executions = [json.loads(path.read_text(encoding="utf-8"))
                  for path in (folder / "raw").rglob("*.execution.json")]
    executions.sort(key=lambda entry: entry["started_at"])
    statuses = [entry["status"] for entry in executions]
    title, expected_resume, expected_success = SCENARIOS[name]
    return {"scenario": name, "description": title, "passed": (
                actual_resume == expected_resume and succeeded == expected_success
                and len(statuses) == len(calls)
                and (name != "timeout" or statuses[0] == "timed_out")),
            "expected_resume": expected_resume, "actual_resume": actual_resume,
            "expected_success": expected_success, "actual_success": succeeded,
            "attempt_count": len(calls), "execution_statuses": statuses, "logs": str(folder)}


def execute_scenario(name, output):
    folder = output / name
    log_path = output / (name + ".log")
    command = [sys.executable, str(Path(__file__).resolve()), "--run-scenario", name, str(folder)]
    try:
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"scenario": name, "passed": False, "error": str(error), "logs": str(log_path)}
    report_path = folder / "rehearsal-report.json"
    if completed.returncode or not report_path.exists():
        return {"scenario": name, "passed": False, "error": "演练进程异常，查看日志", "logs": str(log_path)}
    return json.loads(report_path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="快速演练真实 LCR 重试逻辑；不调用模型，不生成正式测评分数。")
    parser.add_argument("--scenario", choices=["all", *SCENARIOS], default="all")
    parser.add_argument("--output", type=Path, help="新建日志目录；默认 retry-rehearsals/时间戳")
    args = parser.parse_args()
    output = (args.output or ROOT / "retry-rehearsals" / (
        datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])).resolve()
    output.mkdir(parents=True, exist_ok=False)
    save(output / "rehearsal.json", {"simulation": True, "model_calls": 0, "kind": "lcr_retry_rehearsal"})
    names = SCENARIOS if args.scenario == "all" else [args.scenario]
    rows = []
    print("开始重试演练；沿用真实适配器和子进程管理，模拟 LCR 输出，不调用模型。", flush=True)
    for name in names:
        print("演练：" + SCENARIOS[name][0], flush=True)
        row = execute_scenario(name, output)
        rows.append(row)
        print("符合预期" if row["passed"] else "不符合预期，请查看日志", flush=True)
    save(output / "summary.json", {"simulation": True, "scenarios": rows})
    print(f"日志与报告：{output}", flush=True)
    return 0 if all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--simulate-cli"]:
        raise SystemExit(simulate_cli(Path(sys.argv[2]), sys.argv[3:]))
    if sys.argv[1:2] == ["--run-scenario"]:
        save(Path(sys.argv[3]) / "rehearsal-report.json", run_scenario(sys.argv[2], Path(sys.argv[3])))
    else:
        raise SystemExit(main())
