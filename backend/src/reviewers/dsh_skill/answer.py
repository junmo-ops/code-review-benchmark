"""A complete baseline is not a failed or truncated run with no findings."""
import json
from pathlib import PurePosixPath
from .gateway import summarize


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
        raise ValueError('DSH 最终答卷不是文本；未生成完整答卷，原始事件已保留，不自动重试')
    if not text.strip():
        raise ValueError('DSH 最终答卷为空白；completed 仅表示执行结束，不表示检视答卷有效。'
                         '原始事件和会话已保留，不按零意见评分，不自动续写或重试')
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f'DSH 最终答卷不是有效 JSON（第 {error.lineno} 行，第 {error.colno} 列）；'
                         '原始输出已保留，不自动修补或重试') from None


def convert(item, cfg, rows, lines, requests):
    finals, sessions = _completed(rows)
    answer = _parse_final(finals[0].get('text', ''))
    if not isinstance(answer, dict) or answer.get('status') != 'complete' or not isinstance(answer.get('comments'), list):
        raise ValueError('DSH 答卷不是完整 JSON 协议')
    for comment in answer['comments']:
        _finding(comment, lines)
    summary, coverage = summarize(requests)
    return dict(instance_id=item.instance_id, repo=item.repo, base_commit=item.base_commit,
                head_commit=item.head_commit, reviewer='dsh_skill',
                review=dict(status='complete', comments=answer['comments'], summary=summary,
                            usage_coverage=coverage, llm={'model': cfg['review_model']}),
                skill=dict(mode='baseline', loaded=False), dsh=cfg['tool_hashes'],
                session_id=sessions[0]['sessionId'])
