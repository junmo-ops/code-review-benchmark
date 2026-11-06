"""Plan bounded benchmark commands without HTTP dependencies."""
import os
import subprocess
import sys
from pathlib import Path
from .files import read, save


class ExecutionPlanner:
    def __init__(self, context):
        self.context = context

    def command(self, key, stage, profile, task=None, retry=False, init=None):
        if stage=='comparison':
            manifest=read(self.context.run_dir(key)/'experiment.json')
            limit=len(manifest['groups'])*(len(manifest['case_ids'])*(manifest['case_timeout_seconds']+manifest['judge_case_timeout_seconds']+60)+300)
            return [sys.executable,'-m','comparison.runner','--key',key],self.context.environment(profile),limit
        run_id = key.split('/')[-1]
        env = self.context.environment(profile)
        cfg = self.context.config(key) if init is None else init
        if cfg.get('reviewer', cfg.get('reviewer_id')) in {'server_agent', 'cline_skill', 'dsh_skill'}:
            return self._agent_command(stage, run_id, env, cfg, init, task)
        if key.startswith('ccrab/'):
            return self._ccrab_command(stage, run_id, env, cfg)
        if key.startswith('mcr/'):
            entry, args, limit = self._mcr_arguments(stage, run_id, cfg, init, task, retry)
        else:
            entry, args, limit = self._review_arguments(key, stage, run_id, cfg, init, task, retry)
        command = [sys.executable, str(self.context.root/entry), '--timeout-seconds', str(limit)]
        if profile != 'environment':
            command += ['--config', str(self.context.configs/profile)]
        else:
            # Existing wrappers load model.local.json by default. Empty explicit config preserves env semantics.
            path = self.context.state/'environment.local.json'
            save(path, {})
            command += ['--config', str(path)]
        env['LCR_DIAGNOSTICS_DIR'] = str(self.context.run_dir(key)/'diagnostics')
        return command+args, env, limit


    def _agent_command(self, stage, run_id, env, cfg, init, task):
        if cfg.get('reviewer', cfg.get('reviewer_id')) == 'dsh_skill':
            env['BENCH_DSH_OWNER_PID'] = str(os.getpid())
        args=[stage,'--run-id',run_id,'--runs-root',str(self.context.data/'runs')]
        limit=cfg['timeout_minutes']*60+90 if stage in {'review','recover'} else 900
        if stage == 'eval':
            limit = self._scoring_budget(cfg, self.context.data/'runs'/run_id)
        if init is not None:
            args+=['--reviewer',init['reviewer'],'--suite',init['suite'],'--dataset',str(self.context.dataset_path(init['dataset'])),'--evaluator',init['evaluator'],'--model',env.get('LCR_LLM_MODEL',''),'--timeout-minutes',str(init['timeout_minutes'])]
            if init['reviewer'] == 'dsh_skill':
                args += ['--skill-mode', init['skill_mode'],
                         '--dsh-runtime-mode', init.get('dsh_runtime_mode', 'sandbox'),
                         '--dsh-max-output-tokens', str(init.get('dsh_max_output_tokens', 8192))]
                if init.get('skill_version'):
                    from reviewers.dsh_skill.selection import identity_digest
                    args += ['--skill-version', init['skill_version'],
                             '--skill-identity-sha256', identity_digest(init['skill_identity'])]
        if task:args+=['--instance',task]
        if stage=='review':args+=['--external-timeout-seconds',str(limit)]
        return [sys.executable,str(self.context.root/'backend/src/cloud.py')]+args,env,limit


    def _ccrab_command(self, stage, run_id, env, cfg):
        if stage not in {'init','check','run'}:raise ValueError('c-CRAB 不支持该操作')
        if stage=='run':
            from executable.dashboard import verify
            verify(self.context.root,cfg,env)
        env['PATH']=str(self.context.root/'bin')+os.pathsep+env.get('PATH','')
        return [sys.executable,'-m','executable.dashboard',stage,'--run-id',run_id],env,2700 if stage=='run' else 120


    def _mcr_arguments(self, stage, run_id, cfg, init, task, retry):
        limit = 900
        args = [stage, "--run-id", run_id]
        entry = 'backend/src/mcr.py'
        if init is not None:
            args += ['--dataset', 'data/'+init['dataset'], '--reviewer', init['reviewer'], '--max-output-tokens', str(init['max_tokens'])]
        if task:
            args += ['--task', task]
        if stage == 'review':
            limit = self._mcr_review_budget(run_id, cfg, task) if cfg['reviewer'] == 'lcr' else 900
        if retry:
            args.append('--retry')
        return entry, args, limit


    def _mcr_review_budget(self, run_id, cfg, task):
        from multiround.core import load
        from reviewers.lcr.options import case_timeout, patch_file_count
        cases = load(self.context.run_dir('mcr/' + run_id) / 'dataset.jsonl')
        selected = [row for case in cases for row in case['rounds'] if not task or row['task_id'] == task]
        return 60 + sum(case_timeout(cfg.get('lcr_options', {}),
                                    patch_file_count(row['code_diff']['diff']), 10) + 60 for row in selected)


    def _review_arguments(self, key, stage, run_id, cfg, init, task, retry):
        limit = 900
        args = [stage, "--run-id", run_id]
        entry = 'backend/src/local.py'
        if init is not None:
            args += ['--dataset', str(self.context.dataset_path(init['dataset'])), '--suite', init['suite'], '--evaluator', init['evaluator'], '--timeout-minutes', str(init['timeout_minutes']), '--case-jobs', str(init.get('case_jobs', 1))]
            if init['reviewer'] == 'lcr':
                args += ['--max-case-retries', str(init.get('max_case_retries', 1))]
        if task:
            args += ['--instance', task]
        if stage == 'review':
            limit = self._review_budget(key, cfg, task)
        if stage == 'eval':
            limit = self._scoring_budget(cfg, self.context.run_dir(key))
        if retry:
            args.append('--retry')
        return entry, args, limit


    @staticmethod
    def _scoring_budget(cfg, run):
        if cfg.get('suite', {}).get('id') == 'aacr' and cfg.get('evaluator', {}).get('id', 'original') == 'original':
            from suites.aacr.evaluation_resume import scoring_budget
            return scoring_budget(run, cfg)
        return 900


    def _review_budget(self, key, cfg, task):
        # Bound one batch review command by the slowest case, since cases run concurrently.
        from runner.cloud import checked_instances
        items = checked_instances(self.context.run_dir(key)/'dataset.jsonl', cfg['suite']['id'])
        if task:
            items = [i for i in items if i.instance_id == task]
        limits=[]
        attempts = cfg.get('max_case_retries', 1) + 1
        backoff = sum(min(5 * retry, 15) for retry in range(1, attempts))
        for item in items:
            repo = Path(cfg['repos_root']) / item.repo.replace('/', '__')
            base = subprocess.check_output(['git', '-C', str(repo), 'merge-base', item.base_commit, item.head_commit], text=True, timeout=20).strip()
            files = subprocess.check_output(['git', '-C', str(repo), 'diff', '--name-only', '-z', base, item.head_commit, '--'], timeout=20)
            from reviewers.lcr.options import case_timeout
            one_attempt = case_timeout(cfg.get('lcr_options', {}), len([v for v in files.split(b'\0') if v]), cfg['timeout_minutes'])
            limits.append(one_attempt * attempts + backoff)
        case_jobs = max(1, cfg.get('case_jobs', 1))
        batch_budget = sum(max(limits[offset:offset + case_jobs])
                           for offset in range(0, len(limits), case_jobs))
        limit = batch_budget + 60 * len(items) * attempts
        return limit


    def plan(self, job, request, spec):
        if job['action'] == 'comparison':
            return [('comparison', None, None)]
        if spec is not None:
            return [('init', None, spec)]
        key, action = job['key'], job['action']
        if key.startswith('ccrab/'):
            return self._plan_ccrab(action)
        return self._plan_run(key, action, request)


    @staticmethod
    def _plan_ccrab(action):
        return [('check', None, None)] + ([('run', None, None)] if action == 'workflow' else [])


    def _plan_run(self, key, action, request):
        mcr = key.startswith('mcr/')
        case_ids = [c['id'] for c in self.context.cases(key)]
        if request.get('task'):
            case_ids = [request['task']]
        if action == 'check':
            return [('prepare-repos' if mcr else 'check', None, None)]
        if action == 'review' and not request.get('task') and not mcr and self.context.config(key).get('reviewer', 'lcr') == 'lcr':
            return [('review', None, None)]
        if action in {'review','preview','recover'}:
            return [(action, case, None) for case in case_ids]
        if action == 'workflow':
            return self._plan_workflow(key, request, mcr, case_ids)
        if action == 'eval' and self.context.summary(key):
            raise ValueError(self.context._existing_score_message(key))
        return [(action, request.get('task') if mcr else None, None)]


    def _plan_workflow(self, key, request, mcr, case_ids):
        cfg = self.context.config(key)
        steps = [] if mcr and cfg['reviewer'] == 'llm_direct' else [('prepare-repos' if mcr else 'check', None, None)]
        review_steps = [('review', None, None)] if not mcr and cfg.get('reviewer', 'lcr') == 'lcr' and not request.get('task') else [('review', case, None) for case in case_ids]
        eval_steps = [] if self.context.summary(key) or cfg.get('simulation') else [('eval', None, None)]
        return steps + review_steps + eval_steps

