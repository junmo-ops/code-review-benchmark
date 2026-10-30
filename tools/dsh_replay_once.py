"""User-approved single-request wire probe. Never replay a benchmark workflow."""
import hashlib, itertools, json, logging, os, signal, sqlite3, sys, tempfile, time
from pathlib import Path

sys.path[:0] = [str((ROOT := Path(os.environ.get('BENCH_PROJECT_ROOT', '/opt/deployments'))) / 'backend/src'), str(ROOT)]
import httpx; from openai._streaming import SSEDecoder; from webui.service import Dashboard

RUN, CASE = 'aacr-20261029012553703-0231d10a', 'CherryHQ__cherry-studio@5644b00'
FINISH = {k: k for k in ('stop', 'length', 'tool_calls', 'function_call', 'content_filter')}
OUT = dict(delta_chars=0, delta_nonblank=False, message_chars=0, message_nonblank=False, reasoning_chars=0, tool_fragments=0, finish_reason=None, response_id=None, usage={}); JOURNAL = None


def deadline(signum, frame): raise TimeoutError('Probe deadline')


def interrupted(signum, frame): raise InterruptedError('Probe interrupted')


def save(state):
    OUT.update(state=state, updated_unix_seconds=time.time())
    JOURNAL.write(json.dumps(OUT) + '\n'); JOURNAL.flush(); os.fsync(JOURNAL.fileno())


def reserve(path):
    global JOURNAL
    try: JOURNAL = open(path, 'x', opener=lambda p, flags: os.open(p, flags, 0o600))
    except FileExistsError:
        complete = [line for line in path.read_text().splitlines(keepends=True) if line.endswith('\n')]
        OUT.clear(); OUT.update(json.loads(complete[-1]) if complete else {'state': 'reserved_or_incomplete'}); return False
    OUT.update(pid=os.getpid(), started_unix_seconds=time.time(), receipt=str(path)); save('reserved'); return True


def chunks(reply):
    size = 0
    for chunk in reply.iter_bytes():
        size += len(chunk)
        if size > 16 * 1024 * 1024: raise ValueError('Response size limit')
        yield chunk


def count(event):
    choice = (event.get('choices') or [{}])[0]
    for place in ('delta', 'message'):
        text = (part := choice.get(place) or {}).get('content')
        if isinstance(text, str): OUT[place + '_chars'] += len(text); OUT[place + '_nonblank'] |= bool(text.strip())
        reasoning = next((part[k] for k in ('reasoning_content', 'reasoning', 'reasoning_text') if isinstance(part.get(k), str) and part[k]), '')
        OUT['reasoning_chars'] += len(reasoning); OUT['tool_fragments'] += len(part.get('tool_calls') or [])
    if (reason := choice.get('finish_reason')) is not None: OUT['finish_reason'] = FINISH.get(reason, 'other')
    if isinstance(event.get('id'), str): OUT['response_id'] = event['id'][:160]
    if event.get('usage'): OUT['usage'] = {k: v for k, v in event['usage'].items() if type(v) is int and k in {'prompt_tokens', 'completion_tokens', 'total_tokens', 'reasoning_tokens'}}


def main():
    if sys.argv[1:] != ['--execute-once']: raise PermissionError('Explicit execution flag required')
    run = (app := Dashboard(ROOT, load_jobs=False)).data / 'runs' / RUN
    receipt = Path(tempfile.gettempdir()) / ('dsh-replay-' + hashlib.sha256((str(run) + CASE).encode()).hexdigest()[:24] + '.wire-v2.jsonl')
    if not reserve(receipt): return
    cfg = json.loads((run / 'run-config.json').read_text()); payload = json.loads((run / 'raw' / (CASE + '.model-inputs.json')).read_text())[-1]
    with sqlite3.connect((app.state / 'queue.sqlite3').as_uri() + '?mode=ro', uri=True) as db: row = db.execute('SELECT job FROM jobs WHERE run_key=? ORDER BY rowid DESC LIMIT 1', (RUN,)).fetchone()
    endpoint = (env := app.environment(json.loads(row[0])['profile']))['LCR_LLM_BASE_URL']
    if not payload['model'] == env['LCR_LLM_MODEL'] == cfg['review_model'] or hashlib.sha256(endpoint.encode()).hexdigest() != cfg['review_endpoint_sha256'] or not env.get('LCR_LLM_TOKEN'): raise ValueError('Frozen model or endpoint mismatch')
    for sig in (signal.SIGHUP, signal.SIGTERM): signal.signal(sig, interrupted)
    signal.signal(signal.SIGALRM, deadline); signal.alarm(1200)
    save('sending')
    print('sending_once; maximum 20 minutes; repeated execution reads saved result', flush=True)
    with httpx.Client(transport=httpx.HTTPTransport(retries=0), trust_env=False, follow_redirects=False, timeout=1200) as client:
        with client.stream('POST', endpoint.rstrip('/') + '/chat/completions', json=payload, headers={'Authorization': 'Bearer ' + env['LCR_LLM_TOKEN']}) as reply:
            OUT['http_status'] = reply.status_code
            if reply.status_code != 200: raise ValueError('Non-200 response; not retried')
            sse = reply.headers.get('content-type', '').lower().startswith('text/event-stream')
            OUT['wire_format'] = 'sse' if sse else 'json-or-other'; save('reading_response')
            events = (json.loads(e.data) for e in itertools.takewhile(lambda e: e.data.strip() != '[DONE]', SSEDecoder().iter_bytes(chunks(reply))) if e.data.strip()) if sse else [json.loads(b''.join(chunks(reply)))]
            for event in events: count(event)


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    try: main()
    except BaseException as error: OUT['error'] = type(error).__name__
    finally:
        signal.alarm(0)
        if JOURNAL is not None: save('failed' if 'error' in OUT else 'completed'); JOURNAL.close()
        print(json.dumps(OUT, ensure_ascii=False, indent=2), flush=True)
    sys.exit(int(OUT.get('state') != 'completed'))
