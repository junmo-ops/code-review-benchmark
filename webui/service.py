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
        from .profiles import normalize_stream_mode, profile_label
        profiles = []
        for p in sorted(self.configs.glob('model*.local.json')):
            if p.is_symlink():
                continue
            try:
                obj = read(p)
                provider = p.name.split('.', 2)[1] if p.name.count('.') >= 2 else ''
                profiles.append(dict(id=p.name, label=('批次固定配置' if p.name.startswith('model.run-') else profile_label(p)), internal=p.name.startswith('model.run-'), provider=provider, base_url=obj.get('LCR_LLM_BASE_URL', ''), review_ready=bool(obj.get('LCR_LLM_BASE_URL') and obj.get('LCR_LLM_TOKEN') and obj.get('LCR_LLM_MODEL')), review_model=obj.get('LCR_LLM_MODEL', '未设置'),
                                     stream_enabled=normalize_stream_mode(obj.get('LCR_LLM_STREAM', 'false')) == 'true',
                                     judge_model=obj.get('JUDGE_MODEL', '未设置'),
                                     ready=bool(obj.get('LCR_LLM_TOKEN') and obj.get('JUDGE_API_KEY'))))
            except (ValueError, OSError, AttributeError):
                continue
        profiles.append(dict(id='environment', label='平台环境变量', review_ready=bool(os.getenv('LCR_LLM_BASE_URL') and os.getenv('LCR_LLM_TOKEN') and os.getenv('LCR_LLM_MODEL')), review_model=os.getenv('LCR_LLM_MODEL', '未设置'), stream_enabled=os.getenv('LCR_LLM_STREAM', '').lower() in {'true','1','yes','on'},
                             judge_model=os.getenv('JUDGE_MODEL', '未设置'),
                             ready=bool(os.getenv('LCR_LLM_TOKEN') and os.getenv('JUDGE_API_KEY'))))
        return profiles

    def environment(self, profile):
        from local import ALLOWED
        env = dict(os.environ)
        # COS credentials are used only by the workbench service. Do not pass
        # them to LCR, evaluators, or other per-job child processes.
        for name in ('BENCH_CASE_S3_ACCESS_KEY_ID', 'BENCH_CASE_S3_SECRET_ACCESS_KEY', 'BENCH_CASE_S3_SESSION_TOKEN'):
            env.pop(name, None)
        if profile != 'environment':
            if profile not in {p['id'] for p in self.profiles()}:
                raise ValueError('模型配置不存在')
            values = read(self.configs / profile)
            if set(values) - ALLOWED or any(not isinstance(v, str) for v in values.values()):
                raise ValueError('模型配置字段无效')
            values.setdefault('LCR_LLM_STREAM', 'false')
            env.update(values)
        from .profiles import normalize_stream_mode
        env['LCR_LLM_STREAM'] = normalize_stream_mode(env.get('LCR_LLM_STREAM', 'false'))
        env['PYTHONUNBUFFERED'] = '1'
        return env

    def redact(self, text):
        # Both console logs and browsed artifacts are filtered. Never return config contents.
        secrets = [os.getenv(name) for name in ('BENCH_AGENT_TOKEN', 'BENCH_BASIC_PASSWORD', 'BENCH_PROXY_TOKEN',
                                                'BENCH_CSRF_TOKEN', 'BENCH_CASE_S3_ACCESS_KEY_ID',
                                                'BENCH_CASE_S3_SECRET_ACCESS_KEY', 'BENCH_CASE_S3_SESSION_TOKEN')]
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

    def _score_scope(self, key):
        path = self.run_dir(key) / 'evaluation-scope.json'
        if not path.is_file():
            return None
        scope = read(path)
        return scope.get('included', []) if isinstance(scope, dict) else []

    def _score_status(self, key, cases, summary):
        completed = {case['id'] for case in cases if case['status'] == 'complete'}
        total = len(cases)
        scope = self._score_scope(key)
        covered = len(completed.intersection(scope)) if scope is not None else None
        if not summary:
            status = 'reviewed' if len(completed) == total else 'partial' if completed else 'failed' if any(c['status'] != 'pending' for c in cases) else 'pending'
        elif scope is None:
            status = 'scored' if len(completed) == total else 'partial_scored'
            covered = len(completed) if len(completed) == total else None
        elif len(completed) == total and set(scope) == completed:
            status = 'scored'
        else:
            status = 'partial_scored'
        return status, covered

    def _existing_score_message(self, key):
        scope = self._score_scope(key)
        if scope is not None:
            cases = self.cases(key)
            total = len(cases)
            if len(scope) < total:
                return f'已有部分评分，覆盖 {len(scope)}/{total} 个案例；评分证据不可覆盖，新完成的答卷未纳入。若需全量评分，请新建批次。'
        return '该批次已有评分，评分证据不可覆盖。请直接阅读报告；更换裁判或模型需新建批次。'

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
            return self._describe_ccrab(key, cfg)
        mcr = key.startswith('mcr/')
        cases = self.cases(key)
        reports = self.artifacts(key)
        summary = self.summary(key)
        total, completed = len(cases), sum(c['status'] == 'complete' for c in cases)
        score_status, scored_cases = self._score_status(key, cases, summary)
        return dict(key=key, compatibility_warning=self.compatibility(key), name=key.split('/')[-1], suite='mcr' if mcr else cfg.get('suite', {}).get('id', 'aacr'),
                    simulation=cfg.get('simulation',False), reviewer=cfg.get('reviewer', 'lcr'), model=cfg.get('review_model', '—'), judge_model=cfg.get('judge_model', '—'),
                    evaluator='Martian 一对一（内部）' if (self.run_dir(key)/'comparison-protocol.json').exists() else 'MCR 一对一' if mcr else cfg.get('evaluator', {}).get('id', 'original'),
                    created=cfg.get('created_at', datetime.datetime.fromtimestamp((self.run_dir(key)/'run-config.json').stat().st_mtime, datetime.timezone.utc).isoformat()),
                    status='simulation_complete' if cfg.get('simulation') and all(c['status']=='simulation_complete' for c in cases) else score_status,
                    completed=completed, total=total, scored_cases=scored_cases, cases=cases, metrics=summary, artifacts=reports,
                    timeout_minutes=cfg.get('timeout_minutes', 10), concurrency=cfg.get('case_jobs', 1),
                    max_case_retries=cfg.get('max_case_retries', 1 if cfg.get('reviewer', 'lcr') == 'lcr' else 0),
                    profile=read(self.state/'profiles.json').get(key) if (self.state/'profiles.json').exists() else None)

    def _describe_ccrab(self, key, cfg):
        folder = self.run_dir(key)
        result = read(folder/'status.json') if (folder/'status.json').exists() else {}
        status = result.get('status', 'pending')
        metrics = self._ccrab_metrics(status, result)
        created = cfg.get('created_at', datetime.datetime.fromtimestamp(folder.stat().st_mtime, datetime.timezone.utc).isoformat())
        return dict(key=key, name=folder.name, suite='ccrab', reviewer='lcr', model=cfg.get('review_model', '—'),
                    judge_model='不使用语义裁判', evaluator='发布测试（适配）', created=created, status=status,
                    completed=int(bool(result.get('review_executed'))), total=1,
                    cases=[dict(id=cfg['instance_id'], repo=cfg.get('repo', 'networkx/networkx'), status=status, scored=bool(metrics))],
                    metrics=metrics, artifacts=self.artifacts(key), repair_executed=bool(result.get('repair_executed')),
                    can_run=self._ccrab_can_run(key, cfg, status, folder),
                    compatibility_warning=self.compatibility(key) if cfg.get('ui_managed') else '历史 CLI 批次仅供查看；重新执行请新建批次。',
                    profile=read(self.state/'profiles.json').get(key) if (self.state/'profiles.json').exists() else None)

    @staticmethod
    def _ccrab_metrics(status, result):
        if status == 'complete' and result.get('repair_executed') and result.get('test_pass_rate') in (0, 1):
            return {'test_pass_rate': result['test_pass_rate']}
        return {}

    @staticmethod
    def _ccrab_can_run(key, cfg, status, folder):
        return cfg.get('ui_managed', False) and status == 'pending' and not (folder/'execution.started').exists()

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
        enabled = os.environ.get('BENCH_ENABLED_SUITES', '')
        for entry in CATALOG:
            if enabled and entry['id'] not in enabled.split(','):
                continue
            item = dict(entry, datasets=[])
            for filename in entry['datasets']:
                path = self.root / 'data' / filename
                if path.is_file():
                    with path.open(encoding='utf-8') as f:
                        rows = [json.loads(line) for line in f if line.strip()]
                    item['datasets'].append(dict(id=filename, count=len(rows), rounds=sum(len(r.get('rounds', [])) for r in rows)))
            from .case_assets import registered_datasets
            for upload in registered_datasets(self.data):
                if upload.get('suite') == entry['id']:
                    item['datasets'].append(dict(id=upload['id'], name=upload['name'],
                                                 count=upload['count'], rounds=0))
            result.append(item)
        return result

    def dataset_path(self, dataset_id):
        from .case_assets import _registry
        if isinstance(dataset_id, str) and dataset_id.startswith('uploads/'):
            upload = next((row for row in _registry(self.data)['datasets']
                           if row.get('id') == dataset_id), None)
            if upload is None:
                raise ValueError('上传的案例集不存在')
            base = (self.data / 'case-datasets').resolve()
            original = self.data / upload['file']
            path = original.resolve()
            if not path.is_relative_to(base) or not path.is_file() or original.is_symlink():
                raise ValueError('上传的案例集文件不可用')
            return path
        base = (self.root / 'data').resolve()
        path = (base / dataset_id).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise ValueError('案例集文件不可用')
        return path

    def prepare_case_repositories(self, spec, key, action):
        from .case_storage import mode
        if action in {'report', 'export', 'eval', 'recover'} or mode() != 'cos':
            return
        from .case_assets import _registry
        rows = _registry(self.data)['datasets']
        row, suite, digest = self._selected_case_asset(rows, spec, key)
        if row and suite and digest:
            from .case_storage import CaseAssetStorage
            try:
                CaseAssetStorage(self.data).prepare_dataset_repositories(self.data, suite, digest)
            except RuntimeError as error:
                raise ValueError(str(error)) from None

    def _selected_case_asset(self, rows, spec, key):
        if spec is not None:
            suite = spec.get('suite')
            row = next((item for item in rows if item.get('id') == spec.get('dataset')), None)
            digest = row.get('dataset_sha256') if row else None
        else:
            experiment = self.run_dir(key) / 'experiment.json'
            config = read(experiment) if experiment.is_file() else self.config(key)
            suite_value = config.get('suite', {})
            suite = suite_value if isinstance(suite_value, str) else suite_value.get('id')
            digest = config.get('dataset_sha256')
            row = next((item for item in rows if item.get('suite') == suite
                        and item.get('dataset_sha256') == digest), None)
        return row, suite, digest

    def _runtime_storage_state(self):
        path = self.data/'ui-state'/'run-storage-status.json'
        status = read(path) if path.is_file() else {'enabled': False, 'status': 'disabled'}
        pending = next((self.data/'ui-state'/'run-sync-requests').glob('*.json'), None)
        if pending is not None and status.get('status') != 'error':
            status.update(status='pending', message='运行数据等待同步到 COS；同步完成后再更新服务。')
        mode = os.environ.get('BENCH_STORAGE_MODE', 'persistent')
        if os.environ.get('BENCH_SERVER_MODE') and os.environ.get('BENCH_RUN_STORAGE_MODE', 'local').strip().lower() != 'cos':
            mode = 'ephemeral'
        return status, mode

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
            run_storage, storage_mode = self._runtime_storage_state()
            case_assets = None
            if os.environ.get('BENCH_CASE_UPLOAD') == '1':
                from .case_assets import status
                case_assets = status(self.root, self.data)
                if not os.environ.get('BENCH_SERVER_MODE'):
                    case_assets['ready'] = True
                    if not case_assets['datasets']:
                        case_assets['message'] = '本地已有内置案例可直接使用；也可以上传其他案例集。'
            return dict(comparisons=experiments, catalog=self.catalog(), profiles=self.profiles(), runs=sorted(runs, key=lambda r:r['created'], reverse=True), jobs=jobs,
                        active=self.active, case_assets=case_assets, run_storage=run_storage,
                        storage_mode=storage_mode, runtime_ready=any((self.root/name).is_file() for name in ('bin/lcr','bin/lcr.cmd')), platform=sys.platform)

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
                args+=['--reviewer',init['reviewer'],'--suite',init['suite'],'--dataset',str(self.dataset_path(init['dataset'])),'--evaluator',init['evaluator'],'--model',env.get('LCR_LLM_MODEL',''),'--timeout-minutes',str(init['timeout_minutes'])]
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
                args += ['--dataset', str(self.dataset_path(init['dataset'])), '--suite', init['suite'], '--evaluator', init['evaluator'], '--timeout-minutes', str(init['timeout_minutes']), '--case-jobs', str(init.get('case_jobs', 1))]
                if init['reviewer'] == 'lcr':
                    args += ['--max-case-retries', str(init.get('max_case_retries', 1))]
            if task:
                args += ['--instance', task]
            if stage == 'review':
                # Bound one batch review command by the slowest case, since cases run concurrently.
                from runner.cloud import checked_instances
                items = checked_instances(self.run_dir(key)/'dataset.jsonl', cfg['suite']['id'])
                if task:
                    items = [i for i in items if i.instance_id == task]
                limits=[]
                attempts = cfg.get('max_case_retries', 1) + 1
                backoff = sum(min(5 * retry, 15) for retry in range(1, attempts))
                for item in items:
                    repo = Path(cfg['repos_root']) / item.repo.replace('/', '__')
                    base = subprocess.check_output(['git', '-C', str(repo), 'merge-base', item.base_commit, item.head_commit], text=True, timeout=20).strip()
                    files = subprocess.check_output(['git', '-C', str(repo), 'diff', '--name-only', '-z', base, item.head_commit, '--'], timeout=20)
                    one_attempt = max(1, len([v for v in files.split(b'\0') if v])) * cfg['timeout_minutes'] * 60 + 120
                    limits.append(one_attempt * attempts + backoff)
                case_jobs = max(1, cfg.get('case_jobs', 1))
                batch_budget = sum(max(limits[offset:offset + case_jobs])
                                   for offset in range(0, len(limits), case_jobs))
                limit = batch_budget + 60 * len(items) * attempts
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
        if os.environ.get('BENCH_SERVER_MODE') and os.environ.get('BENCH_CASE_UPLOAD') == '1' and request.get('action') not in {'export', 'report'}:
            from .case_assets import status
            if not status(self.root, self.data)['ready']:
                raise ValueError('请先在网页上传并准备至少一份案例 ZIP')
        action = request.get('action')
        request = dict(request)
        role_config = None
        profile = request.get('review_profile', request.get('profile', 'model.local.json')) if action=='create' else request.get('profile', 'model.local.json')
        self.environment(profile)  # Reject invalid profile before queuing.
        if type(request.get('start_after_create',False)) is not bool:
            raise ValueError('开始选项必须为布尔值')
        if action == 'create':
            suite = request.get('suite')
            entry = next((s for s in self.catalog() if s['id'] == suite), None)
            enabled = os.environ.get('BENCH_ENABLED_SUITES', '')
            if enabled and suite not in enabled.split(','):
                raise ValueError('本部署未启用该案例集')
            if not entry or request.get('dataset') not in [dataset['id'] for dataset in entry['datasets']]:
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
            case_jobs = request.get('case_jobs', 1)
            if type(case_jobs) is not int or not 1 <= case_jobs <= 16:
                raise ValueError('案例并发必须为 1–16 的整数')
            if (suite in {'mcr','ccrab'} or reviewer != 'lcr') and case_jobs != 1:
                raise ValueError('案例并发仅适用于单轮 LCR 测评')
            max_case_retries = request.get('max_case_retries', 1 if reviewer == 'lcr' else 0)
            if type(max_case_retries) is not int or not 0 <= max_case_retries <= 2:
                raise ValueError('失败案例自动重试次数必须为 0–2')
            if reviewer != 'lcr' and max_case_retries:
                raise ValueError('失败案例自动重试当前仅支持 LCR')
            spec = dict(suite=suite, dataset=request['dataset'], reviewer=reviewer, evaluator=evaluator, timeout_minutes=timeout, max_tokens=tokens, case_jobs=case_jobs, max_case_retries=max_case_retries)
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
        from .case_import import ensure_idle
        ensure_idle(self)
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

    def _continue_partial_review(self, job, steps, index, stage, code, timed_out=False):
        # AACR's evaluator can score complete answers while explicitly excluding failed cases.
        if (code == 0 and not timed_out) or stage != 'review' or job['action'] not in {'workflow', 'create'}:
            return False
        if job['action'] == 'create' and not job.get('start_after_create'):
            return False
        if self.config(job['key']).get('suite', {}).get('id') != 'aacr':
            return False
        return any(next_stage == 'eval' for next_stage, _, _ in steps[index + 1:])

    def _execute_stage(self, job_id, job, request, stage, task, init, log):
        cmd, env, limit = self.command(job['key'], stage, job['profile'], task,
                                       bool(request.get('retry')), init)
        env['PYTHONUTF8'] = '1'
        log.write(f'\n[{now()}] {stage + (" · " + task if task else "")}\n'
                  + self.redact(' '.join(cmd)) + '\n')
        process = None
        reader = None
        try:
            process_options = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt'
                               else {'start_new_session': True})
            process = subprocess.Popen(cmd, cwd=self.root, env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                       errors='replace', **process_options)
            self.update(job_id, pid=process.pid)
            stream = process.stdout

            def output():
                try:
                    for line in stream:
                        log.write(self.redact(line))
                except (OSError, ValueError):
                    # The main process exit code remains authoritative if a child retains the pipe.
                    pass

            reader = threading.Thread(target=output, daemon=True)
            reader.start()
            deadline = time.monotonic() + limit + 30
            timed_out = False
            while process.poll() is None:
                cancelled = self.cancelled.wait(.15)
                deadline_reached = time.monotonic() > deadline
                if cancelled or deadline_reached:
                    from local import terminate
                    terminate(process)
                    timed_out = deadline_reached and not cancelled
                    break
            reader.join(timeout=10)
            if reader.is_alive():
                log.write('主命令已退出；仍有子进程持有日志流，按主命令退出码继续。\n')
            else:
                stream.close()
            return process.returncode, timed_out
        finally:
            if reader and reader.is_alive():
                reader.join(timeout=1)
            if process is not None and process.poll() is None:
                from local import terminate
                terminate(process)

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
        case_ids = [c['id'] for c in self.cases(key)]
        if request.get('task'):
            case_ids = [request['task']]
        if action == 'check':
            return [('prepare-repos' if mcr else 'check', None, None)]
        if action == 'review' and not request.get('task') and not mcr and self.config(key).get('reviewer', 'lcr') == 'lcr':
            return [('review', None, None)]
        if action in {'review','preview','recover'}:
            return [(action, case, None) for case in case_ids]
        if action == 'workflow':
            return self._plan_workflow(key, request, mcr, case_ids)
        if action == 'eval' and self.summary(key):
            raise ValueError(self._existing_score_message(key))
        return [(action, request.get('task') if mcr else None, None)]

    def _plan_workflow(self, key, request, mcr, case_ids):
        cfg = self.config(key)
        steps = [] if mcr and cfg['reviewer'] == 'llm_direct' else [('prepare-repos' if mcr else 'check', None, None)]
        review_steps = [('review', None, None)] if not mcr and cfg.get('reviewer', 'lcr') == 'lcr' and not request.get('task') else [('review', case, None) for case in case_ids]
        eval_steps = [] if self.summary(key) or cfg.get('simulation') else [('eval', None, None)]
        return steps + review_steps + eval_steps

    def _run_work_stage(self, job_id, job, request, steps, index, stage, task, init, log):
        label = stage + (' · ' + task if task else '')
        self.update(job_id, step=label, step_started_at=now(), completed_steps=index)
        code, timed_out = self._execute_stage(job_id, job, request, stage, task, init, log)
        self.update(job_id, exit_code=code)
        if self.cancelled.is_set():
            return 'cancelled'
        if code != 0 or timed_out:
            if stage == 'comparison':
                raise ValueError('对比实验存在失败或未完成案例，请查看对比报告与日志；不自动重试。')
            if self._continue_partial_review(job, steps, index, stage, code, timed_out):
                reason = '外层时限已到，检视进程已停止' if timed_out else '检视存在未完成案例'
                log.write(f'{reason}；继续评分结构完整的答卷，失败案例将从评分中排除。\n')
                self.update(job_id, completed_steps=index+1, message='检视部分失败，正在评分已完成案例')
                return 'partial'
            if timed_out:
                raise ValueError('页面外层执行时限已到，进程已停止，保留日志')
            raise ValueError('本步骤失败，后续步骤未执行。请查看日志；已完成答卷保留。')
        self.update(job_id, completed_steps=index+1)
        return 'complete'

    def work(self, job_id, request, spec):
        partial_review = False
        log_path = self.state/'jobs'/f'{job_id}.log'
        try:
            job = self.jobs[job_id]
            steps = self.plan(job, request, spec)
            self.update(job_id, status='running', started_at=now(), total_steps=len(steps), message='正在执行')
            with log_path.open('a', encoding='utf-8', buffering=1) as log:
                self.prepare_case_repositories(spec, job['key'], job['action'])
                for index, (stage, task, init) in enumerate(steps):
                    if self.cancelled.is_set():
                        break
                    outcome = self._run_work_stage(job_id, job, request, steps, index, stage, task, init, log)
                    if outcome == 'cancelled':
                        break
                    partial_review = partial_review or outcome == 'partial'
                    if stage=='init' and request.get('start_after_create') and not self.cancelled.is_set():
                        steps.extend(self.plan(dict(job,key=job['key'],action='workflow'),{},None))
                        self.update(job_id,total_steps=len(steps))
            message = ('已停止，已写入的结果保留' if self.cancelled.is_set() else
                       '评分已完成；部分案例检视失败，请查看案例状态与日志' if partial_review else '执行完成')
            self.update(job_id, status='cancelled' if self.cancelled.is_set() else 'succeeded', message=message, ended_at=now())
        except BaseException as exc:
            detail = self.redact(str(exc)) if isinstance(exc, ValueError) else type(exc).__name__+'；请查看日志和批次原始证据'
            with log_path.open('a', encoding='utf-8') as log:
                log.write('\n'+detail+'\n')
            self.update(job_id, status='cancelled' if self.cancelled.is_set() else 'failed', message=detail, ended_at=now())
        finally:
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
