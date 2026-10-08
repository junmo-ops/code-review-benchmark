"""Dashboard facade: allowlisted commands, persisted jobs, no new scoring logic."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from runner.runtime import data_root, config_root

ROOT = Path(__file__).resolve().parents[1]
CATALOG = [
    dict(id='aacr', name='AACR', role='主测', description='真实代码变更 · 缺陷检出与定位', datasets=['pilot1.jsonl', 'pilot5.jsonl']),
    dict(id='swe_prbench_llm', name='SWE-PRBench', role='辅测', description='PR 检视意见 · 统一大模型评分', datasets=['swe-prbench-pilot1.jsonl']),
    dict(id='codefuse_cr', name='CodeFuse-CR-Bench', role='辅测', description='真实 PR · 补充仓库与缺陷覆盖', datasets=['codefuse-pilot1.jsonl']),
    dict(id='mcr', name='MCR', role='多轮专项', description='历史回放 · 缺陷状态与转移', datasets=['mcr-pilot1.jsonl']),
]
CATALOG.append(dict(id='ccrab', name='c-CRAB', role='执行验证专项', description='单例适配实验 · 修复后运行发布测试', datasets=['ccrab-pilot1.jsonl']))
ACTIVE = {'queued', 'running', 'cancelling'}
NAME = re.compile(r'^[A-Za-z0-9_-]{1,100}$')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class Dashboard:
    def __init__(self, root=ROOT, load_jobs=True):
        self.root = Path(root).resolve()
        self.data = data_root(self.root)
        self.configs = config_root(self.root)
        self.state = self.data / 'ui-state'
        self.state.mkdir(parents=True,exist_ok=True)
        self.lock = threading.RLock()
        self.jobs = {}
        self.cancelled = threading.Event()
        self.worker = None
        self.active = None
        # Restart never silently resumes or retries paid requests.
        for path in (self.state.glob('jobs/*.json') if load_jobs else []):
            try:
                job = read(path)
                if job['status'] in ACTIVE:
                    job.update(status='interrupted', message='页面服务曾中断；请检查案例日志，确认旧进程停止后再恢复。', ended_at=now())
                    save(path, job)
                self.jobs[job['id']] = job
            except (ValueError, KeyError, OSError):
                continue

    def profiles(self):
        from .profiles import profile_label
        profiles = []
        for p in sorted(self.configs.glob('model*.local.json')):
            if p.is_symlink():
                continue
            try:
                obj = read(p)
                profiles.append(dict(id=p.name, label=('批次固定配置' if p.name.startswith('model.run-') else profile_label(p)), internal=p.name.startswith('model.run-'), review_ready=bool(obj.get('LCR_LLM_BASE_URL') and obj.get('LCR_LLM_TOKEN') and obj.get('LCR_LLM_MODEL')), review_model=obj.get('LCR_LLM_MODEL', '未设置'),
                                     judge_model=obj.get('JUDGE_MODEL', '未设置'),
                                     ready=bool(obj.get('LCR_LLM_TOKEN') and obj.get('JUDGE_API_KEY'))))
            except (ValueError, OSError, AttributeError):
                continue
        profiles.append(dict(id='environment', label='平台环境变量', review_ready=bool(os.getenv('LCR_LLM_BASE_URL') and os.getenv('LCR_LLM_TOKEN') and os.getenv('LCR_LLM_MODEL')), review_model=os.getenv('LCR_LLM_MODEL', '未设置'),
                             judge_model=os.getenv('JUDGE_MODEL', '未设置'),
                             ready=bool(os.getenv('LCR_LLM_TOKEN') and os.getenv('JUDGE_API_KEY'))))
        return profiles

    def environment(self, profile):
        from local import ALLOWED
        env = dict(os.environ)
        if profile != 'environment':
            if profile not in {p['id'] for p in self.profiles()}:
                raise ValueError('模型配置不存在')
            values = read(self.configs / profile)
            if set(values) - ALLOWED or any(not isinstance(v, str) for v in values.values()):
                raise ValueError('模型配置字段无效')
            env.update(values)
        env['PYTHONUNBUFFERED'] = '1'
        return env

    def redact(self, text):
        # Both console logs and browsed artifacts are filtered. Never return config contents.
        secrets = [os.getenv('BENCH_AGENT_TOKEN')]
        try:
            from reviewers.server_agent.adapter import load_config
            secrets.append(os.getenv(load_config().get('token_env','BENCH_AGENT_TOKEN')))
        except (OSError,ValueError):pass
        for p in self.profiles():
            try:
                env = self.environment(p['id'])
                secrets += [env.get('LCR_LLM_TOKEN'), env.get('JUDGE_API_KEY')]
            except (OSError, ValueError):
                pass
        for value in sorted({v for v in secrets if v}, key=len, reverse=True):
            text = text.replace(value, '[REDACTED]')
        return re.sub(r'(?i)(authorization\s*[:=]\s*bearer\s+)\S+', r'\1[REDACTED]', text)

    def run_dir(self, key):
        if not isinstance(key, str):
            raise ValueError('批次标识无效')
        parts = key.split('/')
        if len(parts) not in (1, 2) or (len(parts) == 2 and parts[0] not in {'mcr', 'ccrab'}) or not NAME.fullmatch(parts[-1]):
            raise ValueError('批次标识无效')
        base = (self.data / 'runs').resolve()
        path = base.joinpath(*parts)
        if path.resolve() != path.absolute() or not path.resolve().is_relative_to(base):
            raise ValueError('不能访问符号链接或项目外路径')
        return path

    def config(self, key):
        folder=self.run_dir(key)
        return read(folder / ('config.json' if key.startswith('ccrab/') and not (folder/'run-config.json').exists() else 'run-config.json'))

    def cases(self, key):
        folder = self.run_dir(key)
        cfg = self.config(key)
        if key.startswith('mcr/'):
            from multiround.core import load, validate_card
            result = []
            for case in load(folder / 'dataset.jsonl'):
                for row in case['rounds']:
                    sub = folder / 'rounds' / row['task_id']
                    status = 'pending'
                    try:
                        if (sub / 'answer.json').exists():
                            validate_card(read(sub / 'answer.json'), row, cfg['reviewer'])
                            status = 'complete'
                        elif (sub / 'review-status.json').exists():
                            status = read(sub / 'review-status.json').get('status', 'failed')
                        elif (sub / 'review').exists():
                            status = 'unfinished_check_required'
                    except (ValueError, KeyError, AttributeError):
                        status = 'invalid_answer'
                    result.append(dict(id=row['task_id'], status=status, scored=(sub / 'score.json').exists(),
                                       repo=case['repo'], round=row['round_id']))
            return result
        from runner.cloud import checked_instances, states
        items = checked_instances(folder / 'dataset.jsonl', cfg.get('suite', {}).get('id', 'aacr'))
        values = states(items, folder)
        return [dict(id=s['instance_id'], status=s['status'], repo=i.repo, scored=False) for s, i in zip(values, items)]

    def summary(self, key):
        root = self.run_dir(key)
        for p in [root / 'metrics.json', root / 'metrics/martian.json', root / 'metrics/metrics_ocr_average.json', *sorted((root / 'metrics').glob('*.json'))]:
            if not p.is_file():
                continue
            try:
                obj = read(p)
                if p.name == 'metrics_ocr_average.json':
                    audit = read(root/'judge-audit.json') if (root/'judge-audit.json').exists() else {}
                    summary = obj.get('summary', {})
                    if audit.get('status') == 'complete' and summary.get('missing_instances') == 0 and summary.get('evaluated_instances') == summary.get('total_instances'):
                        return dict(f1=summary['semantic_f1'], precision=summary['semantic_match_rate'], recall=summary['semantic_recall_rate'])
                if obj.get('status') == 'complete' and obj.get('suite') == 'swe_prbench' and obj.get('results'):
                    values = [r['metrics'] for r in obj['results']]
                    return {k:sum(v[field] for v in values)/len(values) for k,field in [('f1','f1_score'),('precision','precision'),('recall','recall')]}
                if obj.get('status', obj.get('summary', {}).get('status')) == 'complete' and isinstance(obj.get('summary'), dict):
                    return {k: v for k, v in obj['summary'].items() if k in {'f1','precision','recall','tp','fp','fn','transition_accuracy','state_accuracy','state_coverage'}}
            except (ValueError, AttributeError):
                pass
        return {}

    def compatibility(self, key):
        if (self.run_dir(key)/'comparison-protocol.json').exists():
            return '对比实验分组使用独立一对一协议，请在对比实验中查看；不从单批次入口重跑或覆盖评分。'
        try:
            cfg = self.config(key)
            if key.startswith('ccrab/'):
                from executable.dashboard import verify
                verify(self.root,cfg)
                return None
            if key.startswith('mcr/'):
                from multiround.cli import verify
                verify(cfg, self.run_dir(key))
            else:
                from suites.registry import resolve_run_suite
                from scoring.registry import verify
                resolve_run_suite(cfg)
                verify(cfg)
            return None
        except (ValueError, KeyError, OSError):
            return '此历史批次的冻结版本与当前代码不同。可查看结果；继续执行请恢复对应版本或新建批次。'

    def describe(self, key):
        cfg = self.config(key)
        if key.startswith('ccrab/'):
            folder=self.run_dir(key)
            result=read(folder/'status.json') if (folder/'status.json').exists() else {}
            status=result.get('status','pending')
            metrics={}
            if status=='complete' and result.get('repair_executed') and result.get('test_pass_rate') in (0,1):
                metrics['test_pass_rate']=result['test_pass_rate']
            return dict(key=key,name=folder.name,suite='ccrab',reviewer='lcr',model=cfg.get('review_model','—'),judge_model='不使用语义裁判',evaluator='发布测试（适配）',
                created=cfg.get('created_at',datetime.datetime.fromtimestamp(folder.stat().st_mtime,datetime.timezone.utc).isoformat()),status=status,
                completed=int(bool(result.get('review_executed'))),total=1,cases=[dict(id=cfg['instance_id'],repo=cfg.get('repo','networkx/networkx'),status=status,scored=bool(metrics))],
                metrics=metrics,artifacts=self.artifacts(key),repair_executed=bool(result.get('repair_executed')),
                can_run=cfg.get('ui_managed',False) and status=='pending' and not (folder/'execution.started').exists(),compatibility_warning=self.compatibility(key) if cfg.get('ui_managed') else '历史 CLI 批次仅供查看；重新执行请新建批次。',profile=read(self.state/'profiles.json').get(key) if (self.state/'profiles.json').exists() else None)
        mcr = key.startswith('mcr/')
        cases = self.cases(key)
        reports = self.artifacts(key)
        summary = self.summary(key)
        total, completed = len(cases), sum(c['status'] == 'complete' for c in cases)
        return dict(key=key, compatibility_warning=self.compatibility(key), name=key.split('/')[-1], suite='mcr' if mcr else cfg.get('suite', {}).get('id', 'aacr'),
                    simulation=cfg.get('simulation',False), reviewer=cfg.get('reviewer', 'lcr'), model=cfg.get('review_model', '—'), judge_model=cfg.get('judge_model', '—'),
                    evaluator='Martian 一对一（内部）' if (self.run_dir(key)/'comparison-protocol.json').exists() else 'MCR 一对一' if mcr else cfg.get('evaluator', {}).get('id', 'original'),
                    created=cfg.get('created_at', datetime.datetime.fromtimestamp((self.run_dir(key)/'run-config.json').stat().st_mtime, datetime.timezone.utc).isoformat()),
                    status='simulation_complete' if cfg.get('simulation') and all(c['status']=='simulation_complete' for c in cases) else 'scored' if summary and completed == total else 'reviewed' if completed == total else 'partial' if completed else 'failed' if any(c['status'] != 'pending' for c in cases) else 'pending',
                    completed=completed, total=total, cases=cases, metrics=summary, artifacts=reports,
                    timeout_minutes=cfg.get('timeout_minutes', 10), profile=read(self.state/'profiles.json').get(key) if (self.state/'profiles.json').exists() else None)

    def artifacts(self, key):
        root = self.run_dir(key)
        result = []
        # Expose only text evidence from a known run; no arbitrary filesystem read endpoint.
        for p in sorted(root.rglob('*')):
            if not p.is_file() or p.suffix not in {'.md', '.json', '.jsonl', '.txt', '.patch', '.log'}:
                continue
            rel = p.relative_to(root)
            if key.startswith('ccrab/') and rel.parts[0]=='repair-workspace':
                continue
            if p.is_symlink() or not p.resolve().is_relative_to(root.resolve()) or p.name in {'run-config.json', 'dataset.jsonl'}:
                continue
            if len(result) >= 1500:
                break
            result.append(dict(path=str(rel), size=p.stat().st_size, report=p.suffix == '.md'))
        return result

    def artifact(self, key, name):
        allowed = {r['path'] for r in self.artifacts(key)}
        if name not in allowed:
            raise ValueError('文件不在此批次可查看的证据清单中')
        path = self.run_dir(key) / name
        with path.open('rb') as stream:
            raw = stream.read(1024 * 1024)
        return dict(text=self.redact(raw.decode('utf-8', 'replace')), truncated=path.stat().st_size > len(raw))

    def catalog(self):
        result = []
        for entry in CATALOG:
            item = dict(entry, datasets=[])
            for filename in entry['datasets']:
                path = self.root / 'data' / filename
                if path.is_file():
                    with path.open(encoding='utf-8') as f:
                        rows = [json.loads(line) for line in f if line.strip()]
                    item['datasets'].append(dict(id=filename, count=len(rows), rounds=sum(len(r.get('rounds', [])) for r in rows)))
            result.append(item)
        return result

    def snapshot(self):
        runs = []
        paths = list((self.data / 'runs').glob('*/run-config.json')) + list((self.data / 'runs/mcr').glob('*/run-config.json'))
        paths += list((self.data/'runs/ccrab').glob('*/run-config.json'))
        paths += [p for p in (self.data/'runs/ccrab').glob('*/config.json') if not (p.parent/'run-config.json').exists()]
        for path in paths:
            key = str(path.parent.relative_to(self.data / 'runs'))
            try:
                runs.append(self.describe(key))
            except (OSError, ValueError, KeyError, AttributeError) as exc:
                runs.append(dict(key=key, name=path.parent.name, suite='unknown', status='unreadable', error='批次数据不完整或格式不兼容', cases=[], artifacts=[], completed=0, total=0, metrics={}, created=''))
        with self.lock:
            jobs = [dict(j) for j in sorted(self.jobs.values(), key=lambda j: j['created_at'], reverse=True)[:50]]
            for job in jobs:
                start=job.get('started_at')
                if start:
                    end=job.get('ended_at') or now()
                    job['elapsed_seconds']=max(0,int((datetime.datetime.fromisoformat(end)-datetime.datetime.fromisoformat(start)).total_seconds()))
                log=self.state/'jobs'/(job['id']+'.log')
                if log.exists():job['last_output_at']=datetime.datetime.fromtimestamp(log.stat().st_mtime,datetime.timezone.utc).isoformat()
            from comparison.service import list_experiments
            from .experiment_view import decorate
            experiments=decorate(self,list_experiments(self),runs)
            return dict(comparisons=experiments, catalog=self.catalog(), profiles=self.profiles(), runs=sorted(runs, key=lambda r:r['created'], reverse=True), jobs=jobs,
                        active=self.active, runtime_ready=any((self.root/name).is_file() for name in ('bin/lcr','bin/lcr.cmd')), platform=sys.platform)

    def command(self, key, stage, profile, task=None, retry=False, init=None):
        if stage=='comparison':
            manifest=read(self.run_dir(key)/'experiment.json')
            limit=len(manifest['groups'])*(len(manifest['case_ids'])*(manifest['case_timeout_seconds']+manifest['judge_case_timeout_seconds']+60)+300)
            return [sys.executable,'-m','comparison.runner','--key',key],self.environment(profile),limit
        mcr = key.startswith('mcr/')
        run_id = key.split('/')[-1]
        env = self.environment(profile)
        cfg = self.config(key) if init is None else init
        if cfg.get('reviewer',cfg.get('reviewer_id')) in {'server_agent','cline_skill'}:
            args=[stage,'--run-id',run_id,'--runs-root',str(self.data/'runs')]
            limit=cfg['timeout_minutes']*60+90 if stage in {'review','recover'} else 900
            if init is not None:
                args+=['--reviewer',init['reviewer'],'--suite',init['suite'],'--dataset',str(self.root/'data'/init['dataset']),'--evaluator',init['evaluator'],'--model',env.get('LCR_LLM_MODEL',''),'--timeout-minutes',str(init['timeout_minutes'])]
            if task:args+=['--instance',task]
            if stage=='review':args+=['--external-timeout-seconds',str(limit)]
            return [sys.executable,str(self.root/'cloud.py')]+args,env,limit
        if key.startswith('ccrab/'):
            if stage not in {'init','check','run'}:raise ValueError('c-CRAB 不支持该操作')
            if stage=='run':
                from executable.dashboard import verify
                verify(self.root,cfg,env)
            env['PATH']=str(self.root/'bin')+os.pathsep+env.get('PATH','')
            return [sys.executable,'-m','executable.dashboard',stage,'--run-id',run_id],env,2700 if stage=='run' else 120
        limit = 900
        args = [stage, '--run-id', run_id]
        if mcr:
            entry = 'mcr.py'
            if init is not None:
                args += ['--dataset', 'data/'+init['dataset'], '--reviewer', init['reviewer'], '--max-output-tokens', str(init['max_tokens'])]
            if task:
                args += ['--task', task]
            if stage == 'review':
                limit = 780 if cfg['reviewer'] == 'lcr' else 900
            if retry:
                args.append('--retry')
        else:
            entry = 'local.py'
            if init is not None:
                args += ['--dataset', 'data/'+init['dataset'], '--suite', init['suite'], '--evaluator', init['evaluator'], '--timeout-minutes', str(init['timeout_minutes'])]
            if task:
                args += ['--instance', task]
            if stage == 'review':
                # Match the existing per-file timeout policy exactly; fail before a costly call if refs are absent.
                from runner.cloud import checked_instances
                item = next(i for i in checked_instances(self.run_dir(key)/'dataset.jsonl', cfg['suite']['id']) if i.instance_id == task)
                repo = Path(cfg['repos_root']) / item.repo.replace('/', '__')
                base = subprocess.check_output(['git', '-C', str(repo), 'merge-base', item.base_commit, item.head_commit], text=True, timeout=20).strip()
                files = subprocess.check_output(['git', '-C', str(repo), 'diff', '--name-only', '-z', base, item.head_commit, '--'], timeout=20)
                limit = max(1, len([v for v in files.split(b'\0') if v])) * cfg['timeout_minutes'] * 60 + 240
            if retry:
                args.append('--retry')
        command = [sys.executable, str(self.root/entry), '--timeout-seconds', str(limit)]
        if profile != 'environment':
            command += ['--config', str(self.configs/profile)]
        else:
            # Existing wrappers load model.local.json by default. Empty explicit config preserves env semantics.
            path = self.state/'environment.local.json'
            save(path, {})
            command += ['--config', str(path)]
        env['LCR_DIAGNOSTICS_DIR'] = str(self.run_dir(key)/'diagnostics')
        return command+args, env, limit

    def submit(self, request):
        action = request.get('action')
        request = dict(request)
        role_config = None
        profile = request.get('review_profile', request.get('profile', 'model.local.json')) if action=='create' else request.get('profile', 'model.local.json')
        self.environment(profile)  # Reject invalid profile before queuing.
        if type(request.get('start_after_create',False)) is not bool:
            raise ValueError('开始选项必须为布尔值')
        if action == 'create':
            suite = request.get('suite')
            entry = next((s for s in CATALOG if s['id'] == suite), None)
            if not entry or request.get('dataset') not in entry['datasets']:
                raise ValueError('案例集与数据清单不匹配')
            run_id = request.get('name')
            if not isinstance(run_id, str) or not NAME.fullmatch(run_id):
                raise ValueError('批次名只支持字母、数字、下划线和短横线，最长 100 字符')
            key = (suite+'/' if suite in {'mcr','ccrab'} else '')+run_id
            if self.run_dir(key).exists():
                raise ValueError('批次名已存在，请使用新名称')
            reviewer = request.get('reviewer', 'lcr')
            evaluator = request.get('evaluator', 'martian')
            if reviewer not in ({'lcr','llm_direct'} if suite == 'mcr' else {'lcr'} if suite=='ccrab' else {'lcr','server_agent','cline_skill'}):
                raise ValueError('该案例集不支持所选检视对象')
            if reviewer=='cline_skill' and (suite!='aacr' or request['dataset']!='pilot1.jsonl'):
                raise ValueError('Cline + Skill 当前仅开放 AACR 单例试跑')
            if reviewer=='server_agent':
                from reviewers.server_agent.adapter import load_config
                try:load_config()
                except OSError:raise ValueError('请先配置 agent.local.json；参见 docs/服务端Agent接入.md') from None
            if 'review_profile' in request:
                from .profiles import compose_roles
                role_config = compose_roles(self, profile, request.get('judge_profile'), suite!='ccrab')
            if suite=='ccrab':
                from executable.dashboard import identity
                identity(self.root,self.environment(profile))
                if evaluator!='executable':raise ValueError('c-CRAB 必须使用发布测试')
            elif evaluator not in {'martian','original'} or (suite == 'codefuse_cr' and evaluator != 'martian'):
                raise ValueError('该案例集不支持所选裁判')
            timeout = request.get('timeout_minutes', 10)
            tokens = request.get('max_tokens', 32768)
            if type(timeout) is not int or not 1 <= timeout <= 120 or type(tokens) is not int or not 1 <= tokens <= 131072:
                raise ValueError('超时或输出上限不在允许范围内')
            spec = dict(suite=suite, dataset=request['dataset'], reviewer=reviewer, evaluator=evaluator, timeout_minutes=timeout, max_tokens=tokens)
        else:
            key = request.get('key')
            self.config(key)
            spec = None
            warning = self.compatibility(key)
            if warning and (action != 'export' or key.startswith('mcr/')):
                raise ValueError(warning)
            common = {'check','preview','review','eval','report','export','recover','workflow'}
            if key.startswith('mcr/'):
                common = {'check','review','eval','report','export','compare-martian','workflow'}
            if action not in common:
                raise ValueError('不支持的操作')
            if key.startswith('ccrab/'):
                common={'check','workflow'}
                if action=='workflow' and not self.describe(key)['can_run']:
                    raise ValueError('该批次已有执行痕迹；请查看结果或新建批次')
                if request.get('task') or request.get('retry'):raise ValueError('c-CRAB 单例实验不支持恢复或自动重试')
            if action not in common:raise ValueError('不支持的操作')
            if request.get('task') and request['task'] not in {c['id'] for c in self.cases(key)}:
                raise ValueError('案例不属于本批次')
            if action == 'workflow' and request.get('task'):
                raise ValueError('一键检视并评分需要选择全部案例；单例请使用分步检视按钮')
            if action == 'recover' and not request.get('task'):
                raise ValueError('请选择要恢复的单个案例')
        with self.lock:
            if self.active and not getattr(self,'queue_mode',False):
                raise ValueError('已有任务执行中。请等待完成或先停止，避免仓库与日志冲突。')
            if role_config is not None:
                from .profiles import save_run_profile
                profile = save_run_profile(self, role_config)
                request['profile'] = profile
            job_id = uuid.uuid4().hex
            job = dict(id=job_id, key=key, action=action, start_after_create=bool(action=='create' and request.get('start_after_create')),  profile=profile, status='queued', created_at=now(), step='', completed_steps=0, total_steps=0, message='等待执行', exit_code=None)
            try:
                return self.enqueue(job, request, spec)
            except Exception:
                if role_config is not None:
                    (self.configs/profile).unlink(missing_ok=True)
                raise

    def enqueue(self, job, request, spec):
        job_id=job['id'];key=job['key'];profile=job['profile']
        self.jobs[job_id] = job
        self.active = job_id
        self.cancelled.clear()
        save(self.state/'jobs'/f'{job_id}.json', job)
        prefs = read(self.state/'profiles.json') if (self.state/'profiles.json').exists() else {}
        prefs[key] = profile
        save(self.state/'profiles.json', prefs)
        self.worker = threading.Thread(target=self.work, args=(job_id, request, spec), daemon=True)
        self.worker.start()
        return dict(job_id=job_id, key=key)

    def update(self, job_id, **changes):
        with self.lock:
            self.jobs[job_id].update(changes)
            save(self.state/'jobs'/f'{job_id}.json', self.jobs[job_id])

    def plan(self, job, request, spec):
        if job['action']=='comparison':return [('comparison',None,None)]
        if spec is not None:
            return [('init', None, spec)]
        key, action = job['key'], job['action']
        if key.startswith('ccrab/'):
            return [('check',None,None)] + ([('run',None,None)] if action=='workflow' else [])
        mcr = key.startswith('mcr/')
        case_ids = [c['id'] for c in self.cases(key)]
        if request.get('task'):
            case_ids = [request['task']]
        if action == 'check':
            return [('prepare-repos' if mcr else 'check', None, None)]
        if action in {'review','preview','recover'}:
            return [(action, case, None) for case in case_ids]
        if action == 'workflow':
            steps = [] if mcr and self.config(key)['reviewer'] == 'llm_direct' else [('prepare-repos' if mcr else 'check', None, None)]
            return steps + [('review', case, None) for case in case_ids] + ([] if self.summary(key) or self.config(key).get('simulation') else [('eval', None, None)])
        if action == 'eval' and self.summary(key):
            raise ValueError('该批次已有完整评分。请直接阅读报告；更换裁判或模型需新建批次。')
        return [(action, request.get('task') if mcr else None, None)]

    def work(self, job_id, request, spec):
        process = None
        log_path = self.state/'jobs'/f'{job_id}.log'
        try:
            job = self.jobs[job_id]
            steps = self.plan(job, request, spec)
            self.update(job_id, status='running', started_at=now(), total_steps=len(steps), message='正在执行')
            with log_path.open('a', encoding='utf-8', buffering=1) as log:
                for index, (stage, task, init) in enumerate(steps):
                    if self.cancelled.is_set():
                        break
                    label = stage + (' · '+task if task else '')
                    self.update(job_id, step=label, step_started_at=now(), completed_steps=index)
                    cmd, env, limit = self.command(job['key'], stage, job['profile'], task, bool(request.get('retry')), init)
                    log.write(f'\n[{now()}] {label}\n'+self.redact(' '.join(cmd))+'\n')
                    process_options = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt'
                                       else {'start_new_session': True})
                    process = subprocess.Popen(cmd, cwd=self.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                               text=True, encoding='utf-8', errors='replace', **process_options)
                    self.update(job_id, pid=process.pid)
                    def output():
                        for line in process.stdout:
                            log.write(self.redact(line))
                    reader = threading.Thread(target=output, daemon=True)
                    reader.start()
                    deadline = time.monotonic()+limit+30
                    while process.poll() is None:
                        if self.cancelled.wait(.15) or time.monotonic()>deadline:
                            from local import terminate
                            terminate(process)
                            if not self.cancelled.is_set():
                                raise ValueError('页面外层执行时限已到，进程已停止，保留日志')
                            break
                    reader.join(timeout=10)
                    if reader.is_alive():
                        raise ValueError('日志输出流尚未关闭，停止后续步骤以保留现场')
                    process.stdout.close()
                    code = process.returncode
                    self.update(job_id, exit_code=code)
                    if self.cancelled.is_set():
                        break
                    if code != 0:
                        if stage=='comparison':raise ValueError('对比实验存在失败或未完成案例，请查看对比报告与日志；不自动重试。')
                        raise ValueError('本步骤失败，后续步骤未执行。请查看日志；已完成答卷保留。')
                    self.update(job_id, completed_steps=index+1)
                    if stage=='init' and request.get('start_after_create') and not self.cancelled.is_set():
                        steps.extend(self.plan(dict(job,key=job['key'],action='workflow'),{},None))
                        self.update(job_id,total_steps=len(steps))
            self.update(job_id, status='cancelled' if self.cancelled.is_set() else 'succeeded', message='已停止，已写入的结果保留' if self.cancelled.is_set() else '执行完成', ended_at=now())
        except BaseException as exc:
            detail = self.redact(str(exc)) if isinstance(exc, ValueError) else type(exc).__name__+'；请查看日志和批次原始证据'
            with log_path.open('a', encoding='utf-8') as log:
                log.write('\n'+detail+'\n')
            self.update(job_id, status='cancelled' if self.cancelled.is_set() else 'failed', message=detail, ended_at=now())
        finally:
            if process is not None and process.poll() is None:
                from local import terminate
                terminate(process)
            with self.lock:
                self.active = None

    def stop(self, job_id):
        with self.lock:
            if self.active != job_id:
                raise ValueError('任务已结束或不是当前任务')
            self.cancelled.set()
            self.update(job_id, status='cancelling', message='正在停止本任务的进程树…')

    def logs(self, job_id):
        if job_id not in self.jobs:
            raise ValueError('任务不存在')
        path = self.state/'jobs'/f'{job_id}.log'
        if not path.exists():
            return dict(text='', truncated=False)
        with path.open('rb') as f:
            size = path.stat().st_size
            f.seek(max(0, size-200000))
            data = f.read(200000)
        return dict(text=self.redact(data.decode('utf-8', 'replace')), truncated=size>200000)

    def shutdown(self):
        with self.lock:
            active = self.active
        if active:
            self.stop(active)
            if self.worker:
                self.worker.join(timeout=20)
