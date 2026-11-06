"""Validate submissions before persisting a job; independent of HTTP transports."""
import os
import re
import uuid
from .files import now

NAME = re.compile(r'^[A-Za-z0-9_-]{1,100}$')
REVIEWERS = {'mcr': {'lcr', 'llm_direct'}, 'ccrab': {'lcr'}}
SINGLE_REVIEWERS = {'lcr', 'server_agent', 'cline_skill'}
ACTIONS = {'check', 'preview', 'review', 'eval', 'report', 'export', 'recover', 'workflow'}
SPECIAL_ACTIONS = {
    'mcr': {'check', 'review', 'eval', 'report', 'export', 'compare-martian', 'workflow'},
    'ccrab': {'check', 'workflow'},
}


def bounded_integer(value, low, high, message):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(message)
    return value


class SubmissionService:
    def __init__(self, context, compose_roles, save_run_profile):
        self.context = context
        self.compose_roles = compose_roles
        self.save_run_profile = save_run_profile

    def submit(self, request):
        request = dict(request)
        action = request.get('action')
        profile = request.get('profile', 'model.local.json')
        if action == 'create':
            profile = request.get('review_profile', profile)
        self.context.environment(profile)
        if type(request.get('start_after_create', False)) is not bool:
            raise ValueError('开始选项必须为布尔值')
        roles = None
        if action == 'create':
            key, spec = self._create(request, profile)
            if 'review_profile' in request:
                roles = self.compose_roles(self.context, profile, request.get('judge_profile'),
                                           request['suite'] != 'ccrab')
        else:
            key, spec = self._existing(request), None
        return self._enqueue(key, profile, request, spec, roles)

    def _dataset(self, suite, dataset):
        enabled = os.environ.get('BENCH_ENABLED_SUITES', '')
        if enabled and suite not in enabled.split(','):
            raise ValueError('本部署未启用该案例集')
        entry = next((item for item in self.context.catalog() if item['id'] == suite), None)
        if not entry or dataset not in {item['id'] for item in entry['datasets']}:
            raise ValueError('案例集与数据清单不匹配')

    def _new_key(self, suite, name):
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError('批次名只支持字母、数字、下划线和短横线，最长 100 字符')
        key = (suite + '/' if suite in SPECIAL_ACTIONS else '') + name
        if self.context.run_dir(key).exists():
            raise ValueError('批次名已存在，请使用新名称')
        return key

    @staticmethod
    def _reviewer(suite, dataset, reviewer):
        if reviewer not in REVIEWERS.get(suite, SINGLE_REVIEWERS):
            raise ValueError('该案例集不支持所选检视对象')
        if reviewer == 'cline_skill' and (suite != 'aacr' or dataset != 'pilot1.jsonl'):
            raise ValueError('Cline + Skill 当前仅开放 AACR 单例试跑')
        if reviewer == 'server_agent':
            from reviewers.server_agent.adapter import load_config
            try:
                load_config()
            except OSError:
                raise ValueError('请先配置 agent.local.json；参见 docs/服务端Agent接入.md') from None

    def _evaluator(self, suite, evaluator, profile):
        if suite == 'ccrab':
            from executable.dashboard import identity
            identity(self.context.root, self.context.environment(profile))
            if evaluator != 'executable':
                raise ValueError('c-CRAB 必须使用发布测试')
        elif evaluator not in {'martian', 'original'} or (suite == 'codefuse_cr' and evaluator != 'martian'):
            raise ValueError('该案例集不支持所选裁判')

    @staticmethod
    def _budgets(request, suite, reviewer):
        message = '超时或输出上限不在允许范围内'
        timeout = bounded_integer(request.get('timeout_minutes', 10), 1, 120, message)
        tokens = bounded_integer(request.get('max_tokens', 32768), 1, 131072, message)
        case_jobs = bounded_integer(request.get('case_jobs', 1), 1, 16, '案例并发必须为 1–16 的整数')
        if (suite in SPECIAL_ACTIONS or reviewer != 'lcr') and case_jobs != 1:
            raise ValueError('案例并发仅适用于单轮 LCR 测评')
        retries = bounded_integer(request.get('max_case_retries', 1 if reviewer == 'lcr' else 0),
                                  0, 2, '失败案例自动重试次数必须为 0–2')
        if reviewer != 'lcr' and retries:
            raise ValueError('失败案例自动重试当前仅支持 LCR')
        return dict(timeout_minutes=timeout, max_tokens=tokens, case_jobs=case_jobs, max_case_retries=retries)

    def _create(self, request, profile):
        suite, dataset = request.get('suite'), request.get('dataset')
        self._dataset(suite, dataset)
        key = self._new_key(suite, request.get('name'))
        reviewer, evaluator = request.get('reviewer', 'lcr'), request.get('evaluator', 'martian')
        self._reviewer(suite, dataset, reviewer)
        self._evaluator(suite, evaluator, profile)
        spec = dict(suite=suite, dataset=dataset, reviewer=reviewer, evaluator=evaluator,
                    **self._budgets(request, suite, reviewer))
        return key, spec

    def _existing(self, request):
        key, action = request.get('key'), request.get('action')
        self.context.config(key)
        warning = self.context.compatibility(key)
        if warning and (action != 'export' or key.startswith('mcr/')):
            raise ValueError(warning)
        family = key.split('/')[0] if '/' in key else ''
        if action not in SPECIAL_ACTIONS.get(family, ACTIONS):
            raise ValueError('不支持的操作')
        if family == 'ccrab':
            if action == 'workflow' and not self.context.describe(key)['can_run']:
                raise ValueError('该批次已有执行痕迹；请查看结果或新建批次')
            if request.get('task') or request.get('retry'):
                raise ValueError('c-CRAB 单例实验不支持恢复或自动重试')
        self._case_selection(key, action, request.get('task'))
        return key

    def _case_selection(self, key, action, task):
        if task and task not in {case['id'] for case in self.context.cases(key)}:
            raise ValueError('案例不属于本批次')
        if action == 'workflow' and task:
            raise ValueError('一键检视并评分需要选择全部案例；单例请使用分步检视按钮')
        if action == 'recover' and not task:
            raise ValueError('请选择要恢复的单个案例')

    def _enqueue(self, key, profile, request, spec, roles):
        with self.context.lock:
            if self.context.active and not getattr(self.context, 'queue_mode', False):
                raise ValueError('已有任务执行中。请等待完成或先停止，避免仓库与日志冲突。')
            if roles is not None:
                profile = self.save_run_profile(self.context, roles)
                request['profile'] = profile
            job = dict(id=uuid.uuid4().hex, key=key, action=request['action'], profile=profile,
                       start_after_create=bool(request['action'] == 'create' and request.get('start_after_create')),
                       status='queued', created_at=now(), step='', completed_steps=0, total_steps=0,
                       message='等待执行', exit_code=None)
            try:
                return self.context.enqueue(job, request, spec)
            except Exception:
                if roles is not None:
                    (self.context.configs / profile).unlink(missing_ok=True)
                raise
