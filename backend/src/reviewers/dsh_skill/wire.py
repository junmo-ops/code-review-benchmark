"""Content-free observations of the exact buffered response forwarded to DSH."""
import json

from openai._streaming import SSEDecoder

FINISH_REASONS = {'stop', 'length', 'tool_calls', 'function_call', 'content_filter'}
TOKEN_FIELDS = {'prompt_tokens', 'completion_tokens', 'total_tokens', 'reasoning_tokens',
                'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens'}
DETAIL_FIELDS = {'cached_tokens', 'created_cache_tokens', 'reasoning_tokens', 'audio_tokens',
                 'accepted_prediction_tokens', 'rejected_prediction_tokens'}


def _numbers(value, allowed):
    return {key: item for key, item in value.items()
            if key in allowed and type(item) is int and item >= 0}


def _usage(value):
    if not isinstance(value, dict):
        return None
    result = _numbers(value, TOKEN_FIELDS)
    for key in ('prompt_tokens_details', 'completion_tokens_details'):
        if isinstance(value.get(key), dict):
            result[key] = _numbers(value[key], DETAIL_FIELDS)
    return result or None


def _part(value, place, result):
    if not isinstance(value, dict):
        return
    text = value.get('content')
    if isinstance(text, str):
        result[place + '_chars'] += len(text)
        result[place + '_nonblank'] |= bool(text.strip())
    elif text is not None:
        result['nonstring_content'] += 1
    reasoning = next((value[key] for key in ('reasoning_content', 'reasoning', 'reasoning_text')
                      if isinstance(value.get(key), str) and value[key]), '')
    result['reasoning_chars'] += len(reasoning)
    if isinstance(value.get('tool_calls'), list):
        result['tool_fragments'] += len(value['tool_calls'])


def _observe(event, result, redact):
    result['event_count'] += 1
    result['upstream_error'] |= bool(event.get('error'))
    if isinstance(event.get('id'), str):
        result['response_id'] = redact(event['id'])[:160]
    if (usage := _usage(event.get('usage'))) is not None:
        result['usage'] = usage
    choices = event.get('choices')
    if not isinstance(choices, list) or not choices:
        return
    choice = choices[0]
    if not isinstance(choice, dict):
        result['parse_errors'] += 1
        return
    for place in ('delta', 'message'):
        _part(choice.get(place), place, result)
    reason = choice.get('finish_reason')
    if reason is not None:
        result['finish_reason'] = reason if isinstance(reason, str) and reason in FINISH_REASONS else 'other'


def _decode(data, result, redact):
    try:
        event = json.loads(data)
    except (ValueError, RecursionError):
        result['parse_errors'] += 1
        return
    if isinstance(event, dict):
        _observe(event, result, redact)
    else:
        result['parse_errors'] += 1


def _stream(body, result, redact):
    for event in SSEDecoder().iter_bytes(iter([body])):
        if event.data.strip() == '[DONE]':
            result['done_marker'] = True
            break
        if event.data.strip():
            _decode(event.data, result, redact)


def response_observations(body, content_type, redact=lambda text: text):
    sse = content_type.lower().startswith('text/event-stream')
    result = dict(wire_format='sse' if sse else 'json-or-other', event_count=0, parse_errors=0,
                  done_marker=False, upstream_error=False, delta_chars=0, delta_nonblank=False,
                  message_chars=0, message_nonblank=False, reasoning_chars=0, tool_fragments=0,
                  nonstring_content=0, finish_reason=None, response_id=None, usage=None)
    try:
        if sse:
            _stream(body, result, redact)
        else:
            _decode(body, result, redact)
    except (ValueError, RecursionError):
        result['parse_errors'] += 1
    result['parse_status'] = 'malformed' if result['parse_errors'] else 'ok'
    if not result['event_count'] and not result['parse_errors']:
        result['parse_status'] = 'no_events'
    return result
