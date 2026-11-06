"""Validate ordinary batch creation before enqueueing any work or saving credentials."""
import os

LIMIT_ERRORS = {
    'timeout_minutes': '超时或输出上限不在允许范围内',
    'max_tokens': '超时或输出上限不在允许范围内',
    'dsh_max_output_tokens': 'DSH 单次输出上限必须为 1–65536 的整数',
    'case_jobs': '案例并发必须为 1–16 的整数',
    'max_case_retries': '失败案例自动重试次数必须为 0–2',
}


def _selection(dashboard, request):
    suite = request.get('suite')
    entry = next((s for s in dashboard.catalog() if s['id'] == suite), None)
    enabled = os.environ.get('BENCH_ENABLED_SUITES', '')
    if enabled and suite not in enabled.split(','):
        raise ValueError('本部署未启用该案例集')
    if not entry or request.get('dataset') not in [d['id'] for d in entry['datasets']]:
        raise ValueError('案例集与数据清单不匹配')
    return suite


def _reviewer(request, suite):
    reviewer = request.get('reviewer', 'lcr')
    choices = {'mcr': {'lcr', 'llm_direct'}, 'ccrab': {'lcr'},
               'aacr': {'lcr', 'server_agent', 'cline_skill', 'dsh_skill'}}
    if reviewer not in choices.get(suite, {'lcr', 'server_agent', 'cline_skill'}):
        raise ValueError('该案例集不支持所选检视对象')
    if reviewer == 'cline_skill' and (suite != 'aacr' or request['dataset'] != 'pilot1.jsonl'):
        raise ValueError('Cline + Skill 当前仅开放 AACR 单例试跑')
    if reviewer == 'server_agent':
        from reviewers.server_agent.adapter import load_config
        try:
            load_config()
        except OSError:
            raise ValueError('请先配置 agent.local.json；参见 docs/服务端Agent接入.md') from None
    if reviewer == 'dsh_skill':
        from reviewers.dsh_skill.selection import validate_mode
        validate_mode(request.get('skill_mode'), request.get('skill_version'))
    elif request.get('skill_mode') is not None or request.get('skill_version') is not None:
        raise ValueError('Skill 模式仅适用于 DSH + Skill')
    return reviewer


def _scoring(dashboard, request, suite, profile):
    evaluator = request.get('evaluator', 'martian')
    if suite == 'ccrab':
        from executable.dashboard import identity
        identity(dashboard.root, dashboard.environment(profile))
        if evaluator != 'executable':
            raise ValueError('c-CRAB 必须使用发布测试')
    elif evaluator not in {'martian', 'original'} or (suite == 'codefuse_cr' and evaluator != 'martian'):
        raise ValueError('该案例集不支持所选裁判')
    return evaluator


def _integer(request, name, default, lower, upper):
    value = request.get(name, default)
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(LIMIT_ERRORS[name])
    return value


def _limits(request, suite, reviewer):
    jobs = _integer(request, 'case_jobs', 1, 1, 16)
    retries = _integer(request, 'max_case_retries', 1 if reviewer == 'lcr' else 0, 0, 2)
    if (suite in {'mcr', 'ccrab'} or reviewer != 'lcr') and jobs != 1:
        raise ValueError('案例并发仅适用于单轮 LCR 测评')
    if reviewer != 'lcr' and retries:
        raise ValueError('失败案例自动重试当前仅支持 LCR')
    return dict(timeout_minutes=_integer(request, 'timeout_minutes', 10, 1, 120),
                max_tokens=_integer(request, 'max_tokens', 32768, 1, 131072),
                case_jobs=jobs, max_case_retries=retries, **_dsh_limits(request, reviewer))


def _dsh_limits(request, reviewer):
    if reviewer == 'dsh_skill':
        return dict(dsh_max_output_tokens=_integer(request, 'dsh_max_output_tokens', 8192, 1, 65536))
    if request.get('dsh_max_output_tokens') is not None:
        raise ValueError('DSH 单次输出上限仅适用于 DSH + Skill')
    return {}


def prepare(dashboard, request, profile):
    from .service import NAME
    suite = _selection(dashboard, request)
    run_id = request.get('name')
    if not isinstance(run_id, str) or not NAME.fullmatch(run_id):
        raise ValueError('批次名只支持字母、数字、下划线和短横线，最长 100 字符')
    key = (suite + '/' if suite in {'mcr', 'ccrab'} else '') + run_id
    if dashboard.run_dir(key).exists():
        raise ValueError('批次名已存在，请使用新名称')
    reviewer = _reviewer(request, suite)
    evaluator = _scoring(dashboard, request, suite, profile)
    role_config = None
    if 'review_profile' in request:
        from .profiles import compose_roles
        role_config = compose_roles(dashboard, profile, request.get('judge_profile'), suite != 'ccrab')
    spec = dict(suite=suite, dataset=request['dataset'], reviewer=reviewer, evaluator=evaluator,
                **_limits(request, suite, reviewer))
    if reviewer == 'dsh_skill':
        from reviewers.dsh_skill.runtime import runtime_mode
        spec['dsh_runtime_mode'] = runtime_mode()
        spec['skill_mode'] = request['skill_mode']
        if request['skill_mode'] == 'skill':
            from .skill_assets import identity
            spec.update(skill_version=request['skill_version'],
                        skill_identity=identity(dashboard, request['skill_version']))
    return key, spec, role_config
