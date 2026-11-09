"""Dashboard facade: allowlisted commands, persisted jobs, no new scoring logic."""
import datetime
import json
import os
from pathlib import Path
import re
import sys
import threading
from runner.runtime import data_root, config_root
from application.files import read, save, now

from runner.paths import PROJECT_ROOT as ROOT
CATALOG = [
    dict(id='aacr', name='AACR', role='主测', description='真实代码变更 · 缺陷检出与定位', datasets=['pilot1.jsonl', 'pilot5.jsonl']),
    dict(id='swe_prbench_llm', name='SWE-PRBench', role='辅测', description='PR 检视意见 · 统一大模型评分', datasets=['swe-prbench-pilot1.jsonl']),
    dict(id='codefuse_cr', name='CodeFuse-CR-Bench', role='辅测', description='真实 PR · 补充仓库与缺陷覆盖', datasets=['codefuse-pilot1.jsonl']),
    dict(id='mcr', name='MCR', role='多轮专项', description='历史回放 · 缺陷状态与转移', datasets=['mcr-pilot1.jsonl']),
]
CATALOG.append(dict(id='ccrab', name='c-CRAB', role='执行验证专项', description='单例适配实验 · 修复后运行发布测试', datasets=['ccrab-pilot1.jsonl']))
ACTIVE = {'queued', 'running', 'cancelling'}
NAME = re.compile(r'^[A-Za-z0-9_-]{1,100}$')


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
        from application.queries import RunQueries
        self.queries = RunQueries(self)
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
            return self._mcr_cases(folder, cfg)
        from runner.cloud import checked_instances, states
        items = checked_instances(folder / 'dataset.jsonl', cfg.get('suite', {}).get('id', 'aacr'))
        values = states(items, folder)
        from .scoring_scope import scored_case_ids
        from reviewers.lcr.coverage import cached_case_diagnostics
        scored = scored_case_ids(folder)
        return [dict(id=s['instance_id'], status=s['status'], repo=i.repo,
                     scored=None if scored is None else s['instance_id'] in scored,
                     **cached_case_diagnostics(folder, i.safe_id)) for s, i in zip(values, items)]

    def _mcr_cases(self, folder, cfg):
        from multiround.core import load, validate_card
        from reviewers.lcr.coverage import multi_round_diagnostics
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
                                   repo=case['repo'], round=row['round_id'], **multi_round_diagnostics(sub)))
        return result

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

    def _score_lock_reason(self, key, cases, summary):
        return self._existing_score_message(key, cases) if summary else None

    def _existing_score_message(self, key, cases=None):
        scope = self._score_scope(key)
        if scope is not None:
            cases = self.cases(key) if cases is None else cases
            total = len(cases)
            if len(scope) < total:
                return f'已有部分评分，覆盖 {len(scope)}/{total} 个案例；评分证据不可覆盖，新完成的答卷未纳入。若需全量评分，请新建批次。'
        return '该批次已有评分，评分证据不可覆盖。请直接阅读报告；更换裁判或模型需新建批次。'

    def compatibility(self, key):
        if (self.run_dir(key)/'comparison-protocol.json').exists():
            return '对比实验分组使用独立一对一协议，请在对比实验中查看；不从单批次入口重跑或覆盖评分。'
        if self._case_resources_purged(key):
            return '该批次依赖的案例集或仓库资源已被永久清理，不能续跑检视或重新评分；历史日志、答卷和已生成结果仍可查看。重新导入对应资源后请新建批次。'
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
        from runner.evaluation import summary as execution_summary
        evaluation = execution_summary(self.run_dir(key), cfg, [c['id'].replace('/', '__') for c in cases])
        return dict(key=key, evaluation=evaluation, compatibility_warning=self.compatibility(key), name=key.split('/')[-1], suite='mcr' if mcr else cfg.get('suite', {}).get('id', 'aacr'),
                    simulation=cfg.get('simulation',False), reviewer=cfg.get('reviewer', 'lcr'), model=cfg.get('review_model', '—'), judge_model=cfg.get('judge_model', '—'),
                    skill_mode=cfg.get('skill_mode'), skill_version=cfg.get('skill_version'),
                    skill_name=cfg.get('skill_identity', {}).get('name'),
                    dsh_runtime_version=cfg.get('tool_hashes', {}).get('version') if cfg.get('reviewer') == 'dsh_skill' else None,
                    dsh_runtime_mode=cfg.get('dsh_policy', {}).get('runtime_mode'),
                    evaluator='Martian 一对一（内部）' if (self.run_dir(key)/'comparison-protocol.json').exists() else 'MCR 一对一' if mcr else cfg.get('evaluator', {}).get('id', 'original'),
                    created=cfg.get('created_at', datetime.datetime.fromtimestamp((self.run_dir(key)/'run-config.json').stat().st_mtime, datetime.timezone.utc).isoformat()),
                    status='simulation_complete' if cfg.get('simulation') and all(c['status']=='simulation_complete' for c in cases) else score_status,
                    completed=completed, total=total, scored_cases=scored_cases, cases=cases, metrics=summary, artifacts=reports,
                    score_locked_reason=self._score_lock_reason(key, cases, summary),
                    timeout_minutes=cfg.get('timeout_minutes', 10), concurrency=cfg.get('case_jobs', 1),
                    max_case_retries=cfg.get('max_case_retries', 1 if cfg.get('reviewer', 'lcr') == 'lcr' else 0),
                    case_resources_purged=self._case_resources_purged(key, cfg),
                    profile=read(self.state/'profiles.json').get(key) if (self.state/'profiles.json').exists() else None)

    def _case_resources_purged(self, key, config=None):
        config = config or self.config(key)
        digest = config.get('dataset_sha256')
        suite_data = config.get('suite', {})
        suite = suite_data if isinstance(suite_data, str) else suite_data.get('id') if isinstance(suite_data, dict) else None
        if not isinstance(digest, str) or not isinstance(suite, str):
            return False
        from .case_assets import _registry
        return any(row.get('suite') == suite and row.get('dataset_sha256') == digest
                   and row.get('purged') for row in _registry(self.data)['datasets'] if isinstance(row, dict))

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
        from runner.evidence import evidence_files
        for p in evidence_files(root):
            if not p.is_file() or p.suffix not in {'.md', '.json', '.jsonl', '.txt', '.patch', '.log'}:
                continue
            rel = p.relative_to(root)
            if key.startswith('ccrab/') and rel.parts[0]=='repair-workspace':
                continue
            if p.is_symlink() or not p.resolve().is_relative_to(root.resolve()) or p.name in {'run-config.json', 'dataset.jsonl'}:
                continue
            if len(result) >= 1500:
                break
            result.append(dict(path=rel.as_posix(), size=p.stat().st_size, report=p.suffix == '.md'))
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
                           if row.get('id') == dataset_id and not row.get('archived')), None)
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

    def prepare_case_repositories(self, spec, key, action, progress=None):
        from .case_storage import mode
        if action in {'report', 'export', 'eval', 'recover'} or mode() != 'cos':
            return
        from .case_assets import _registry
        rows = _registry(self.data)['datasets']
        row, suite, digest = self._selected_case_asset(rows, spec, key)
        if row and suite and digest:
            from .case_storage import CaseAssetStorage
            try:
                from runner.diagnostics import phase
                with phase('benchmark.prepare_repositories', key=key):
                    CaseAssetStorage(self.data).prepare_dataset_repositories(self.data, suite, digest, progress=progress)
            except RuntimeError as error:
                raise ValueError(str(error)) from None
        elif suite == 'aacr':
            self._prepare_builtin_repositories(spec, key, suite, progress)

    def _prepare_builtin_repositories(self, spec, key, suite, progress):
        from .case_storage import CaseAssetStorage, _sha256
        if spec is not None:
            if spec.get('reviewer') == 'server_agent' and not _agent_needs_local_repositories():
                return
            dataset = self.dataset_path(spec['dataset'])
        else:
            config = self.config(key)
            if config.get('reviewer') == 'server_agent' and not _agent_needs_local_repositories():
                return
            dataset = self.run_dir(key) / 'dataset.jsonl'
            if _sha256(dataset) != config.get('dataset_sha256'):
                raise ValueError('冻结案例清单校验失败；禁止按其他案例恢复仓库')
        try:
            CaseAssetStorage(self.data).prepare_builtin_repositories(self.data, suite, dataset, progress)
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

    def snapshot(self, compact=False):
        runs = [] if compact else self.queries.all(details=True)
        with self.lock:
            jobs = [dict(j) for j in sorted(self.jobs.values(), key=lambda j: j['created_at'], reverse=True)[:50]]
        for job in jobs:
            start=job.get('started_at')
            if start:
                end=job.get('ended_at') or now()
                job['elapsed_seconds']=max(0,int((datetime.datetime.fromisoformat(end)-datetime.datetime.fromisoformat(start)).total_seconds()))
            log=self.state/'jobs'/(job['id']+'.log')
            if log.exists():job['last_output_at']=datetime.datetime.fromtimestamp(log.stat().st_mtime,datetime.timezone.utc).isoformat()
        from application.experiments import list_experiments
        from .experiment_view import decorate
        experiments=decorate(self,list_experiments(self, compact=compact),runs)
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
                    dsh_runtime_mode=os.environ.get('BENCH_DSH_RUNTIME_MODE', 'sandbox'),
                    active=self.active, case_assets=case_assets, run_storage=run_storage,
                    storage_mode=storage_mode, runtime_ready=any((self.root/name).is_file() for name in ('bin/lcr','bin/lcr.cmd')), platform=sys.platform)

    def command(self, key, stage, profile, task=None, retry=False, init=None):
        from application.planning import ExecutionPlanner
        return ExecutionPlanner(self).command(key, stage, profile, task, retry, init)


    def _require_submission_resources(self, request):
        if os.environ.get('BENCH_SERVER_MODE') and os.environ.get('BENCH_CASE_UPLOAD') == '1' and request.get('action') not in {'export', 'report'}:
            from .case_assets import status
            if not status(self.root, self.data)['ready']:
                raise ValueError('请先在网页上传并准备至少一份案例 ZIP')

    def submit(self, request):
        self._require_submission_resources(request)
        from application.submissions import SubmissionService
        from .creation import prepare
        from .profiles import save_run_profile
        return SubmissionService(self, prepare, save_run_profile).submit(request)

    def enqueue(self, job, request, spec):
        from .case_import import benchmark_submission
        with benchmark_submission(self):
            if spec is not None:
                job['resource_dataset'] = spec.get('dataset')
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
        from application.planning import ExecutionPlanner
        return ExecutionPlanner(self).plan(job, request, spec)


    def work(self, job_id, request, spec):
        from application.execution import JobExecutor
        return JobExecutor(self).work(job_id, request, spec)

    def stop(self, job_id, actor=None):
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
        from .case_import import stop
        stop(self)
        with self.lock:
            active = self.active
        if active:
            self.stop(active)
            if self.worker:
                self.worker.join(timeout=20)


def _agent_needs_local_repositories():
    from reviewers.server_agent.adapter import load_config, company
    return load_config()['contract'] == company.CONTRACT
