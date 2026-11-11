"""Validate submissions before persisting a job; independent of HTTP transports."""
import uuid
from .files import now

ACTIONS = {'check', 'preview', 'review', 'eval', 'report', 'export', 'recover', 'workflow'}
SPECIAL_ACTIONS = {
    'mcr': {'check', 'review', 'eval', 'report', 'export', 'compare-martian', 'workflow'},
    'ccrab': {'check', 'workflow'},
}


class SubmissionService:
    def __init__(self, context, prepare_creation, save_run_profile):
        self.context = context
        self.prepare_creation = prepare_creation
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
            key, spec, roles = self.prepare_creation(self.context, request, profile)
        else:
            key, spec = self._existing(request), None
        return self._enqueue(key, profile, request, spec, roles)

    def _existing(self, request):
        key, action = request.get('key'), request.get('action')
        cfg = self.context.config(key)
        if cfg.get('reviewer') == 'dsh_skill' and request.get('retry'):
            raise ValueError('DSH 失败检视须新建批次，不在原批次重跑')
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
