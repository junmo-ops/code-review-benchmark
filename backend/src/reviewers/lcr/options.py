"""Frozen LCR 2.x execution options shared by ordinary and multi-round runs."""
import os


def _integer(env, name, default, minimum):
    value = int(env.get(name, default))
    if value < minimum:
        raise ValueError(f"{name} 必须 >= {minimum}")
    return value


def freeze_options(env=None):
    env = os.environ if env is None else env
    effort = env.get('BENCH_LCR_EFFORT', 'low')
    if effort not in {'low', 'medium', 'high'}:
        raise ValueError('BENCH_LCR_EFFORT 必须为 low、medium 或 high')
    tools = _integer(env, 'BENCH_LCR_MAX_TOOLS', 50, 0)
    if 0 < tools < 50:
        raise ValueError('LCR 工具上限只能为 0（模板默认）或 >= 50')
    cap = _integer(env, 'BENCH_LCR_CASE_TIMEOUT_SECONDS', 0, 0)
    if effort != 'low' and cap == 0:
        raise ValueError('medium/high 需显式配置 BENCH_LCR_CASE_TIMEOUT_SECONDS 案例总时限')
    stream = str(env.get('LCR_LLM_STREAM', 'false')).lower()
    if stream not in {'true', '1', 'yes', 'on', 'false', '0', 'no', 'off'}:
        raise ValueError('LCR_LLM_STREAM 必须为布尔值')
    return dict(effort=effort, max_tools=tools, stream=stream in {'true', '1', 'yes', 'on'},
                request_timeout_seconds=_integer(env, 'BENCH_LCR_REQUEST_TIMEOUT_SECONDS', 2700, 1),
                max_tokens_budget=_integer(env, 'BENCH_LCR_MAX_TOKENS_BUDGET', 0, 0),
                case_timeout_seconds=cap)


def command_options(options):
    flags = ['--effort', options['effort'], '--max-tools', str(options['max_tools']),
             '--request-timeout', str(options['request_timeout_seconds']) + 's',
             '--max-tokens-budget', str(options['max_tokens_budget'])]
    if not options['stream']:
        flags.append('--no-stream')
    return flags


def case_timeout(options, file_count, timeout_minutes):
    return options.get('case_timeout_seconds', 0) or max(1, file_count) * timeout_minutes * 60 + 120
