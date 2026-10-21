"""Offline LCR diagnostics. Never infer root causes from elapsed time alone."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from .session_logs import collect_session
from .coverage import read_manifest, coverage_issues, coverage_text, coverage_details


def redact(text):
    text = str(text)
    for key, value in os.environ.items():
        if len(value) >= 8 and re.search(r"(?:API_KEY|TOKEN|SECRET|PASSWORD)$", key, re.I):
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)(Bearer\s+)[^\s\"']+", r"\1[REDACTED]", text)
    return text


def parse_lcr_output(raw):
    """Accept clean JSON or exactly one LCR JSON object amid progress lines."""
    clean = raw.lstrip("\ufeff").strip()
    try:
        result = json.loads(clean)
        if not isinstance(result, dict):
            raise ValueError("LCR 输出顶层不是对象")
        return result
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    candidates = []
    end = 0
    for match in re.finditer(r"(?m)^[ \t]*\{", clean):
        start = match.end() - 1
        if start < end:
            continue
        try:
            value, size = decoder.raw_decode(clean[start:])
        except json.JSONDecodeError:
            continue
        end = start + size
        if (isinstance(value, dict) and isinstance(value.get("status"), str)
                and ("comments" in value or "summary" in value or "warnings" in value)):
            candidates.append(value)
    if len(candidates) != 1:
        raise ValueError("LCR 未返回唯一、完整的结果 JSON；请检查原始日志（不会自动重跑）")
    return candidates[0]


def error_kind(message):
    lower = message.lower()
    if re.search(r"\b504\b", lower) or "gateway timeout" in lower:
        return "网关超时（504）"
    if "error parsing response json" in lower or ("json" in lower and "eof" in lower):
        return "模型响应 JSON 解析失败"
    if "context deadline exceeded" in lower:
        return "任务或请求截止时间到达"
    if re.search(r"\b401\b|\b403\b", lower):
        return "接口认证或权限失败"
    if re.search(r"\b429\b", lower):
        return "接口限流（429）"
    if "timed out" in lower or "timeout" in lower:
        return "超时（具体层级见原文）"
    if "check your llm configuration" in lower or "all " in lower and "review(s) failed" in lower:
        return "LCR 未提供具体原因"
    return "其他错误（见原文）"


def read_optional(path, errors):
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return value
    except (OSError, ValueError):
        errors.append({"kind": "日志文件无法读取", "file": None, "message": path.name})
        return None


def command_value(command, flag):
    if isinstance(command, list) and command.count(flag) == 1:
        index = command.index(flag)
        if index + 1 < len(command):
            return command[index + 1]
    return None


def _parse_case_logs(stdout, stderr, outcome, errors):
    result = {}
    if stdout.strip():
        try:
            result = parse_lcr_output(stdout)
        except ValueError as error:
            errors.append({"kind": "输出解析失败", "file": None, "message": str(error)})
    for warning in (result.get("warnings") or []):
        if isinstance(warning, dict):
            message = str(warning.get("message", "未提供错误详情"))
            errors.append({"kind": error_kind(message), "file": warning.get("file"), "message": redact(message)})
    log_text = stderr + "\n" + stdout
    for match in re.finditer(r"(?m)^\[lcr\]\s+Subtask error for (.+?): (.+)$", log_text):
        errors.append({"kind": error_kind(match[2]), "file": match[1], "message": redact(match[2])})
    for match in re.finditer(r"(?m)^\[lcr\]\s+Error: (.+)$", log_text):
        errors.append({"kind": error_kind(match[1]), "file": None, "message": redact(match[1])})
    retry_history = outcome.get("retry_history") or []
    for retry in retry_history:
        message = redact(retry.get("error", "自动重试前的失败未提供详情"))
        errors.append({"kind": "自动重试后恢复：" + error_kind(message),
                       "file": None, "message": message})
    if outcome.get("error"):
        errors.append({"kind": "适配器记录", "file": None, "message": redact(outcome["error"])})
    # Deduplicate warnings that are also emitted to stderr.
    errors = list({(e["file"], e["message"]): e for e in errors}.values())
    return result, errors, retry_history, log_text


def _merge_session_errors(session, errors):
    for failure in session.get("errors", []):
        message = str(failure.get("error", "未提供详情"))
        evidence = {"kind": error_kind(message), "file": failure.get("filePath"), "message": message}
        if not any(e["file"] == evidence["file"] and e["message"] == message for e in errors):
            errors.append(evidence)


def _valid_saved_answer(item, answer, review):
    return (answer.get("instance_id") == item.instance_id
                    and answer.get("base_commit") == item.base_commit and answer.get("head_commit") == item.head_commit
                    and review.get("status") == "complete" and isinstance(review.get("comments"), list))


def _case_status(manual, state, result, execution, session, valid_answer, has_records):
    if manual and state in ("running", "starting", "interrupted"):
        status = "人工中止（用户确认）"
    elif state == "timed_out":
        status = "外层进程超时"
    elif state == "interrupted":
        status = "用户取消"
    elif state in ("running", "starting"):
        status = "执行记录未收尾（需确认进程状态）"
    else:
        return _result_status(result, execution, session, valid_answer, has_records)
    return status


def _has_subtask_errors(result):
    return result.get('status') in ('completed_with_errors', 'completed_with_warnings') or any(
        isinstance(w, dict) and w.get('type') == 'subtask_error' for w in (result.get('warnings') or []))


def _result_status(result, execution, session, valid_answer, has_records):
    lcr_status = result.get("status")
    if lcr_status == "budget_exceeded":
        status = "Token 预算耗尽（部分结果保留，不自动重试）"
    elif _has_subtask_errors(result):
        status = "部分完成"
    elif execution.get("exit_code") not in (None, 0):
        status = "LCR 失败"
    elif coverage_issues(session.get("review_manifest")):
        status = "部分完成（文件覆盖未完成）"
    elif lcr_status == "skipped":
        status = "文件均被跳过（未调用模型检视）"
    elif session.get("errors"):
        status = "部分完成（答卷已保存但会话有失败项）" if valid_answer else "部分完成（会话有失败项）"
    elif valid_answer:
        status = "完整成功"
    elif lcr_status in ("success", "completed") and execution.get("exit_code") == 0:
        status = "输出成功，待转换或核验"
    elif has_records:
        status = "未完成或适配失败"
    else:
        status = "未执行"
    return status


def _append_status_errors(state, status, execution, errors):
    if state == "timed_out":
        errors.insert(0, {"kind": "外层进程超时", "file": None,
                         "message": f"适配器达到 {execution.get('process_timeout_seconds')} 秒上限。"})
    if status in ("LCR 失败", "部分完成", "未完成或适配失败") and not errors:
        errors.append({"kind": "LCR 未提供具体原因", "file": None,
                       "message": "当前保存的输出没有逐文件异常，不能从耗时或通用提示反推出原因。"})


def _case_details(item, folder, status, result, execution, session, session_id,
                  manual, review, retry_history, log_text, errors):
    lcr_status = result.get("status")
    tool_errors = [redact(line) for line in log_text.splitlines()
                   if re.match(r"^\[lcr\]\s+\w+ failed:", line)]
    summary = result.get("summary") or review.get("summary") or {}
    if not isinstance(summary, dict):
        summary = {}
    command = execution.get("command_without_credentials") or []
    return dict(instance_id=item.instance_id, status=status, lcr_status=lcr_status,
        exit_code=execution.get("exit_code"), elapsed_seconds=execution.get("elapsed_seconds"),
        elapsed_reported_by_lcr=summary.get("elapsed"),
        timeout_minutes_actual=command_value(command, "--timeout"),
        process_timeout_seconds=execution.get("process_timeout_seconds"),
        audience_actual=command_value(command, "--audience"),
        repository_actual=command_value(command, "--repo"),
        session_id=session.get("session_id") or session_id, session_evidence=session,
        manual_stop=manual, review_manifest=session.get("review_manifest"),
        comments=len(result["comments"]) if isinstance(result.get("comments"), list) else None,
        automatic_retries=len(retry_history),
        tokens=summary.get("total_tokens"), tool_calls=result.get("tool_calls"), errors=errors,
        tool_errors=tool_errors, evidence_prefix=f"raw/{item.safe_id}",
        previous_attempts=len(list((folder / "attempts").glob(item.safe_id + "-*"))))


def inspect_case(item, run):
    folder = run / "raw"
    errors = []
    execution = read_optional(folder / (item.safe_id + ".execution.json"), errors) or {}
    outcome = read_optional(folder / (item.safe_id + ".outcome.json"), errors) or {}
    stdout_path = folder / (item.safe_id + ".stdout.json")
    stderr_path = folder / (item.safe_id + ".stderr.txt")
    stdout = stdout_path.read_text(encoding="utf-8-sig", errors="replace") if stdout_path.exists() else ""
    stderr = stderr_path.read_text(encoding="utf-8-sig", errors="replace") if stderr_path.exists() else ""
    result = {}
    result, errors, retry_history, log_text = _parse_case_logs(stdout, stderr, outcome, errors)
    sessions = re.findall(r"\[lcr\]\s+Session:\s+([\w-]+)", log_text)
    session_id = result.get("session_id") or (sessions[-1] if sessions else None)
    session = collect_session(item, run, execution, session_id, redact)
    manifest = read_manifest(result, session, redact)
    errors.extend(coverage_issues(manifest))
    session = dict(session, review_manifest=manifest)
    _merge_session_errors(session, errors)
    manual = read_optional(run / "manual-stops" / (item.safe_id + ".json"), errors)
    state = execution.get("status")
    lcr_status = result.get("status")
    answer_path = run / "results" / (item.safe_id + ".json")
    answer = read_optional(answer_path, errors) or {}
    review = answer.get("review") if isinstance(answer.get("review"), dict) else {}
    valid_answer = _valid_saved_answer(item, answer, review)
    status = _case_status(manual, state, result, execution, session, valid_answer, bool(execution or outcome or stdout or stderr))
    _append_status_errors(state, status, execution, errors)
    return _case_details(item, folder, status, result, execution, session, session_id,
                         manual, review, retry_history, log_text, errors)


def _manifest_lines(manifest, cell):
    if manifest is None:
        return []
    lines = [coverage_text(manifest), '', '| 文件 | 处置 | 原因 |', '|---|---|---|']
    for item in coverage_details(manifest):
        lines.append('| ' + ' | '.join(cell(item[key]) for key in ('file', 'kind', 'message')) + ' |')
    return lines + ['']


def _case_summary_lines(row, run, cell):
    lines = []
    lines += [f"## {row['instance_id']}", "", f"状态：{row['status']}；LCR 状态：{cell(row['lcr_status'])}；退出码：{cell(row['exit_code'])}。", "",
              f"会话：`{cell(row['session_id'])}`；实际任务超时：{cell(row['timeout_minutes_actual'])} 分钟；外层上限：{cell(row['process_timeout_seconds'])} 秒。", ""]
    lines += _manifest_lines(row.get("review_manifest"), cell)
    session = row["session_evidence"]
    if session.get("match") == "matched":
        lines += [f"会话最后记录：{cell(session.get('last_event_at'))}；事件计数：{cell(session.get('event_counts'))}。", ""]
        for request in session.get("slowest_requests", []):
            lines += [f"- 模型请求：{cell(request.get('timestamp'))}；文件：{cell(request.get('filePath'))}；耗时：{cell(request.get('duration_ms'))} 毫秒；事件：{cell(request.get('type'))}"]
        if session.get("pending_requests"):
            lines += ["", "存在尚无对应返回记录的请求；这不证明进程仍在运行。"]
        lines += ["", "最后事件摘要：", "", "```json", json.dumps(session.get("recent_events", []),ensure_ascii=False,indent=2), "```", ""]
    else:
        lines += ["会话证据：" + cell(session.get("note", session.get("match"))), ""]
    if row.get("manual_stop"):
        lines += ["人工中止备注：" + cell(row["manual_stop"].get("reason")), ""]
    for error in row["errors"]:
        lines += [f"- {error['kind']}；文件：{cell(error['file'])}", "",
                  "    " + error["message"].replace("\n", "\n    "), ""]
    if row["tool_errors"]:
        lines += ["工具局部错误（不自动视为整例失败原因）：", ""]
        lines += ["- " + cell(message) for message in row["tool_errors"]]
        lines.append("")
    for suffix, label in ((".stdout.json", "原始标准输出"), (".stderr.txt", "原始错误输出"),
                          (".execution.json", "执行配置及耗时"), (".events.jsonl", "带时间的过程日志")):
        relative = row["evidence_prefix"] + suffix
        if (run / relative).exists():
            lines.append(f"- [{label}]({relative})")
    lines.append("")
    return lines


def write_summary(instances, run):
    config = read_optional(run / "run-config.json", []) or {}
    rows = [inspect_case(item, run) for item in instances]
    counts = Counter(row["status"] for row in rows)
    data = dict(generated_at=datetime.now(timezone.utc).isoformat(), run_id=run.name,
                repository_mode=config.get("repository_mode", "isolated" if config.get("workspace_isolation") else "shared"),
                case_jobs=config.get("case_jobs", 1), file_concurrency=config.get("concurrency", 1),
                max_case_retries=config.get("max_case_retries", 1),
                total=len(rows), complete=counts["完整成功"], completion_rate=counts["完整成功"] / len(rows) if rows else 0,
                counts=dict(counts), cases=rows)
    for row in rows:
        target = run / "diagnostics" / (row["instance_id"].replace("/", "__") + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output = run / "run-summary.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    def cell(value):
        return str(value if value is not None else "未提供").replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    budgets = sorted({str(r["timeout_minutes_actual"]) for r in rows if r["timeout_minutes_actual"] is not None})
    lines = ["# LCR 运行汇总", "", f"轮次：`{run.name}`", "",
             f"案例并发配置：{data['case_jobs']}；每案例文件并发：{data['file_concurrency']}；失败案例最多自动重试：{data['max_case_retries']} 次。", "",
             f"完整成功：{data['complete']}/{len(rows)}（{data['completion_rate']:.1%}）。这不是质量得分。", "",
             "实际任务超时配置：" + ("、".join(budgets) + " 分钟。" if budgets else "日志未提供。") + "以执行命令为准，不按轮次名称推断。", "",
             "| 案例 | 状态 | 自动重试 | 耗时（秒） | 意见数 | 明确错误 |", "|---|---|---:|---:|---:|---|"]
    for row in rows:
        kinds = "；".join(dict.fromkeys(e["kind"] for e in row["errors"])) or "无已记录错误"
        lines.append("| " + " | ".join(cell(v) for v in [row["instance_id"], row["status"], row["automatic_retries"], row["elapsed_seconds"], row["comments"], kinds]) + " |")
    lines += ["", "耗时未提供时不使用最后一次心跳冒充最终耗时。部分意见保留，但不作为完整答卷评分。", ""]
    for row in rows:
        lines += _case_summary_lines(row, run, cell)
    lines += ["## 日志边界", "", "过程时间戳是适配器收到输出的时间，不代表模型请求的精确开始/结束时间。"
              "未输出的请求耗时、响应体及服务端异常无法从旧日志恢复；须由 LCR 或网关按会话/请求记录提供。"
              "未自动执行恢复或重试。", ""]
    (run / "run-summary.zh-CN.md").write_text("\n".join(lines), encoding="utf-8")
    return data
