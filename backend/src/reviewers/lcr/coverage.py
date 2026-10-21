"""Allowlisted file coverage from LCR JSON or a uniquely matched local session."""
import json
from pathlib import Path

STATES = ('selected', 'completed', 'reused', 'failed', 'waived', 'skipped')
REASONS = {
    'default_path': '默认规则排除（包括测试文件、生成文件等）',
    'user_rule': '用户规则排除', 'extension': '不支持的文件类型',
    'deleted': '文件已删除', 'too_large': '变更内容超过大小限制',
    'binary': '二进制文件', 'secret': '敏感文件路径',
    'reached the maximum tool-request rounds without finishing': '工具请求轮数耗尽，任务未完成',
    'stopped after repeated rounds without a usable tool result': '连续多轮没有可用工具结果',
    'stopped because context compression exceeded its threshold': '上下文压缩超过阈值',
    'reached the aggregate token budget before finishing': 'budget_exceeded：Token 总预算耗尽',
}


def _safe_item(item, redact):
    return {key: redact(str(item[key]))[:4000] for key in
            ('path', 'old_path', 'classification', 'reason') if item.get(key) is not None}


def normalize_manifest(value, redact):
    if not isinstance(value, dict) or not isinstance(value.get('coverage'), dict):
        return None
    raw = value['coverage']
    coverage = {state: [_safe_item(item, redact) for item in raw.get(state, []) if isinstance(item, dict)]
                for state in STATES if isinstance(raw.get(state), list)}
    failure = value.get('run_failure')
    return dict(terminal_state=redact(str(value.get('terminal_state', 'unknown'))), coverage=coverage,
                run_failure=_safe_item(failure, redact) if isinstance(failure, dict) else None)


def read_manifest(result, session, redact):
    direct = normalize_manifest(result.get('manifest'), redact)
    if direct is not None:
        return direct
    if session.get('match') != 'matched' or not session.get('source_file'):
        return None
    manifest = None
    try:
        with Path(session['source_file']).open(encoding='utf-8-sig', errors='replace') as source:
            for line in source:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get('type') == 'session_end':
                    manifest = normalize_manifest(record.get('run_manifest'), redact)
    except OSError:
        return None
    return manifest


def coverage_issues(manifest):
    if manifest is None:
        return []
    issues = []
    failure = manifest.get('run_failure')
    if failure:
        reason = failure.get('reason') or failure.get('classification') or '运行未完成'
        message = REASONS.get(reason, reason)
        if failure.get('classification') == 'budget':
            message = 'budget_exceeded：' + message
        issues.append(dict(kind='运行终止', file=None, message=message))
    for state in ('failed', 'waived'):
        for item in manifest['coverage'].get(state, []):
            reason = item.get('reason') or item.get('classification') or '未提供具体原因'
            issues.append(dict(kind='检视未完成' if state == 'failed' else '检视未执行',
                               file=item.get('path'), message=REASONS.get(reason, reason)))
    return issues


def coverage_text(manifest):
    if manifest is None:
        return None
    counts = {state: len(manifest['coverage'].get(state, [])) for state in STATES}
    return (f"文件：完成 {counts['completed']}，复用 {counts['reused']}，失败 {counts['failed']}，"
            f"未执行 {counts['waived']}，跳过 {counts['skipped']}")


def coverage_details(manifest):
    if manifest is None:
        return []
    rows = coverage_issues(manifest)
    for item in manifest['coverage'].get('skipped', []):
        reason = item.get('reason') or '未提供具体原因'
        rows.append(dict(kind='文件跳过', file=item.get('path'), message=REASONS.get(reason, reason)))
    return rows


def cached_case_diagnostics(folder, safe_id):
    try:
        data = json.loads((folder / 'diagnostics' / (safe_id + '.json')).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return coverage_fields(data.get('review_manifest'))


def coverage_fields(manifest):
    if not isinstance(manifest, dict):
        return {}
    return dict(review_coverage=coverage_text(manifest), review_details=coverage_details(manifest))


def multi_round_diagnostics(folder):
    try:
        data = json.loads((folder / 'review/lcr-coverage.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return coverage_fields(data.get('review_manifest')) if isinstance(data, dict) else {}
