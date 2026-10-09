"""Read LCR session evidence locally; do not copy prompts, code or credentials."""
from collections import Counter, deque
from datetime import datetime, timedelta
import json
from pathlib import Path


def stamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError, AttributeError):
        return None


def repo_key(value):
    return str(value or '').replace('\\', '/').rstrip('/').casefold()


def collect_session(item, run, execution, session_id, redact, session_root=None):
    root = session_root if session_root is not None else Path.home()/'.leancodereview/sessions'
    start = stamp(execution.get('started_at'))
    if start is None:
        events = run/'raw'/(item.safe_id+'.events.jsonl')
        try:
            with events.open(encoding='utf-8-sig') as f:
                start = stamp(json.loads(f.readline()).get('time'))
        except (OSError, ValueError):
            pass
    end = stamp(execution.get('ended_at'))
    command = execution.get('command_without_credentials') or []
    repo = None
    if '--repo' in command and command.index('--repo')+1 < len(command):
        repo = command[command.index('--repo')+1]
    candidates = []
    for path in root.glob('**/*.jsonl'):
        if session_id and path.stem != session_id:
            continue
        try:
            with path.open(encoding='utf-8-sig') as f:
                header = json.loads(f.readline())
            if not isinstance(header, dict) or header.get('type') != 'session_start':
                continue
            if header.get('diffFrom') != item.base_commit or header.get('diffTo') != item.head_commit:
                continue
            if session_id and header.get('sessionId') != session_id:
                continue
            if repo and repo_key(header.get('cwd')) != repo_key(repo):
                continue
            at = stamp(header.get('timestamp'))
            if not session_id:
                # Never guess from the newest file: require execution time AND repo.
                if start is None or not repo or at is None or at < start-timedelta(seconds=5):
                    continue
                if at > start+timedelta(minutes=2):
                    continue
                if end and at > end+timedelta(seconds=5):
                    continue
            candidates.append(path)
        except (OSError, ValueError, TypeError):
            continue
    if len(candidates) != 1:
        return {'match': 'ambiguous' if candidates else 'not_found', 'candidate_count': len(candidates),
                'note': '无法唯一匹配本次会话；未使用其他批次记录。'}
    path = candidates[0]
    counts = Counter(); recent = deque(maxlen=8); llm_errors=[]; checkpoints={}; slow=[]; end_record=None
    requests={}; total_ms=0; invalid=0; session=None; last=None
    try:
        with path.open(encoding='utf-8-sig') as source:
            for line in source:
                try:
                    record=json.loads(line)
                    if not isinstance(record,dict):
                        raise ValueError('not object')
                except ValueError:
                    invalid+=1
                    continue
                kind=record.get('type','unknown'); counts[kind]+=1
                session=session or record.get('sessionId'); last=record.get('timestamp') or last
                brief={k:record[k] for k in ('timestamp','type','filePath','duration_ms','duration_seconds','request_no','status','error') if k in record}
                if 'error' in brief: brief['error']=redact(brief['error'])[:4000]
                if kind=='llm_request':
                    requests[record.get('uuid')]=brief
                elif kind in ('llm_response','llm_error'):
                    requests.pop(record.get('parentUuid'),None)
                    ms=record.get('duration_ms')
                    if isinstance(ms,(int,float)):
                        total_ms+=ms
                        slow.append(brief); slow=sorted(slow,key=lambda r:r.get('duration_ms',0),reverse=True)[:5]
                if kind=='tool_call':
                    brief['tool']=record.get('name') or record.get('tool')
                    brief['ok']=record.get('ok')
                if kind == 'llm_error':
                    # A request error is diagnostic evidence, not proof that
                    # the file stayed incomplete. LCR may recover or finish
                    # the file through another task after this event.
                    llm_errors.append(brief)
                elif kind in ('review_item_done', 'review_item_reused', 'review_item_failed'):
                    fingerprint = record.get('fingerprint')
                    checkpoint_key = ('fingerprint', fingerprint) if fingerprint else (
                        'path', record.get('filePath') or record.get('newPath') or record.get('oldPath') or '<unknown>')
                    # Keep the latest checkpoint for each diff. A later done
                    # or reused record means an earlier failure was recovered.
                    checkpoints[checkpoint_key] = brief
                if kind=='session_end':end_record=brief
                if kind!='session_start':recent.append(brief)
    except OSError as error:
        return {'match':'read_failed','note':type(error).__name__}
    failures = [record for record in checkpoints.values() if record.get('type') == 'review_item_failed']
    return {'match':'matched','session_id':session,'source_file':str(path),'last_event_at':last,
            'event_counts':dict(counts),'llm_recorded_duration_ms':total_ms,'slowest_requests':slow,
            'pending_requests':list(requests.values())[-5:], 'errors':failures,
            'llm_errors':llm_errors,
            'session_end':end_record,'recent_events':list(recent),'unparsed_lines':invalid,
            'note':'仅摘录元数据；没有保存提示词、代码或完整模型响应。未结束请求只表示尚无对应返回记录。'}
