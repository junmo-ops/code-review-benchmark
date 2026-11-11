"""A complete baseline is not a failed or truncated run with no findings."""
import json
from pathlib import PurePosixPath
from .gateway import summarize
from .jsonextract import extract_json_object


class FinalAnswerFormatError(ValueError):
    """A completed turn with an unusable final serialization."""


def _finding(comment, lines):
    if not isinstance(comment, dict):
        raise ValueError('意见必须为对象')
    path, start, end, content = [comment.get(k) for k in ('path', 'start_line', 'end_line', 'content')]
    if not isinstance(path, str) or '\\' in path or ':' in path or PurePosixPath(path).is_absolute():
        raise ValueError('意见路径无效')
    if path not in lines or type(start) is not int or type(end) is not int:
        raise ValueError('意见必须定位 head 中的普通文件及整数行号')
    if not 1 <= start <= end <= lines[path] or not isinstance(content, str) or not content.strip():
        raise ValueError('意见行号越界或内容为空')


def _completed(rows):
    finals = [r for r in rows if r.get('type') == 'final']
    ends = [r for r in rows if r.get('type') == 'status' and r.get('phase') == 'turn_end']
    sessions = [r for r in rows if r.get('type') == 'session']
    if len(finals) != 1 or len(sessions) != 1 or not ends:
        raise ValueError('DSH 缺少完整结束记录')
    if ends[-1].get('reason', {}).get('kind') != 'completed':
        raise ValueError('DSH 未正常完成检视')
    return finals, sessions


def _parse_final(text):
    if not isinstance(text, str):
        raise FinalAnswerFormatError('DSH 最终答卷不是文本；未生成完整答卷，原始事件已保留，是否重试由冻结执行策略决定')
    if not text.strip():
        raise FinalAnswerFormatError('DSH 最终答卷为空白；completed 仅表示执行结束，不表示检视答卷有效。'
                         '原始事件和会话已保留，不按零意见评分，是否重试由冻结执行策略决定')
    try:
        return _loads_detect_duplicates(text)
    except json.JSONDecodeError as error:
        line, column = error.lineno, error.colno
    # The review model occasionally wraps the required object in prose (e.g.
    # "I have verified ...\n\n{...}").  Salvage the last complete top-level
    # object; anything else is still a failed answer, never a complete one.
    wrapped = extract_json_object(text)
    if wrapped is not None:
        try:
            return _loads_detect_duplicates(wrapped)
        except json.JSONDecodeError:
            pass
    raise FinalAnswerFormatError(f'DSH 最终答卷不是有效 JSON（第 {line} 行，第 {column} 列）；'
                     f'提取末段 JSON 对象也未成功；原始输出已保留，是否重试由冻结执行策略决定') from None


def _loads_detect_duplicates(text):
    """Parse JSON with ``json.loads`` semantics but reject repeated keys.

    The archived gofr case delivered a complete-looking object whose final
    comment repeated ``start_line``/``end_line``/``content`` after the real
    values (a stray second-comment fragment was merged into the first object).
    Plain ``json.loads`` keeps the last occurrence, silently turning the
    finding into an empty out-of-range comment.  Duplicate keys are therefore
    a malformed-reply error -- the retryable ''DSH 最终答卷…'' class -- never a
    silently "repaired" answer.
    """
    duplicates = []

    def record(pairs):
        mapping = {}
        seen = set()
        for key, value in pairs:
            if key in seen:
                duplicates.append(key)
            seen.add(key)
            mapping[key] = value
        return mapping

    parsed = json.loads(text, object_pairs_hook=record)
    if duplicates:
        keys = ', '.join(sorted(set(duplicates)))
        raise ValueError(f'DSH 最终答卷包含重复 JSON 键：{keys}；原始输出已保留，不自动修补或重试')
    return parsed


def verdict(rows):
    """Gate the reply shape before capability vetoes.

    A malformed final reply is an answer-format failure -- the retryable
    ``DSH 最终答卷…`` class used to re-run a case once -- not evidence that
    the Skill depended on a disabled capability. Reading the verdict here
    first keeps ''Skill 依赖禁用能力'' for finals that actually declare or
    demonstrate a missing capability (see loading.require_supported).
    """
    finals, _ = _completed(rows)
    return _parse_final(finals[0].get('text', ''))


def convert(item, cfg, rows, lines, requests):
    finals, sessions = _completed(rows)
    answer = _parse_final(finals[0].get('text', ''))
    if not isinstance(answer, dict) or answer.get('status') != 'complete' or not isinstance(answer.get('comments'), list):
        raise ValueError('DSH 答卷不是完整 JSON 协议')
    for comment in answer['comments']:
        _finding(comment, lines)
    summary, coverage = summarize(requests)
    # ``skill`` is the baseline placeholder; adapter.recover_answer replaces it
    # with loading.verify_loading's result: single-valued {mode:'skill',
    # version, name, loaded, evidence} for one-member batches, {mode:'skill',
    # members:[{version, name, loaded, evidence}]} for N>=2 combinations, and
    # {mode:'baseline', loaded:False} for baseline (identical to this
    # placeholder). Members' evidence entries carry form/session_seq/request/
    # text_sha256. Discriminate: mode first, then presence of a members list.
    return dict(instance_id=item.instance_id, repo=item.repo, base_commit=item.base_commit,
                head_commit=item.head_commit, reviewer='dsh_skill',
                review=dict(status='complete', comments=answer['comments'], summary=summary,
                            usage_coverage=coverage, raw_format_compliant=_raw_format_compliant(finals[0].get('text', '')), llm={'model': cfg['review_model']}),
                skill=dict(mode='baseline', loaded=False), dsh=cfg['tool_hashes'],
                session_id=sessions[0]['sessionId'])


def _raw_format_compliant(text):
    try:
        return isinstance(json.loads(text), dict)
    except (ValueError, TypeError):
        return False
