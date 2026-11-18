"""Existing HTTP contract backed by shared run records instead of local job files."""
import json
from pathlib import Path
import tempfile
import uuid
from application.files import now, read
from webui.service import Dashboard, ROOT
from .settings import concurrency_limit, services
from .contracts import identifier


class ClusterQueries:
    def __init__(self, app):
        self.app = app

    def all(self, details=False):
        return [self.app.describe(row['run_id']) for row in self.app.records.values()]

    def detail(self, key):
        return self.app.describe(key) if key in self.app.records else None

    def page(self, page=1, page_size=10, suite='', search=''):
        if type(page) is not int or page < 1 or not 1 <= page_size <= 100:
            raise ValueError('分页参数无效')
        rows = self.all()
        rows = [row for row in rows if (not suite or row['suite'] == suite)
                and search.casefold() in (row['name'] + row['model']).casefold()]
        total = len(rows)
        page = min(page, max(1, (total + page_size - 1) // page_size))
        return dict(items=rows[(page-1)*page_size:page*page_size], total=total, page=page, page_size=page_size)


class ClusterDashboard(Dashboard):
    queue_mode = True
    cluster_mode = True

    def __init__(self, root=ROOT):
        super().__init__(root, load_jobs=False)
        self.queue, self.store, self.cluster = services(root)
        limit = self.queue.bootstrap(concurrency_limit())
        print(f'集群就绪：{self.cluster["scope"]}；全局并发上限 {limit}', flush=True)
        self.records = {}
        self.queries = ClusterQueries(self)
        self.sync()
        from .web_catalog import WebCatalog
        from .web_uploads import SharedUploads
        self.shared_catalog = True
        self.web_catalog = WebCatalog(self)
        self.shared_uploads = SharedUploads(self.web_catalog)

    def sync(self):
        self.records = {row['run_id']: row for row in self.queue.runs()}
        self.jobs = {}
        for row in self.records.values():
            job = dict(row['job'])
            status = row['status']
            job.update(status={'preparing':'queued', 'reviewing':'running', 'scoring':'running',
                               'ready':'succeeded'}.get(status, status),
                       step={'preparing':'创建批次','reviewing':'分布式检视','scoring':'评分与报告'}.get(status, ''),
                       message=_status_message(status))
            if status != 'preparing':
                job['started_at'] = row['created_at'].isoformat() + '+00:00'
            if status in {'ready', 'succeeded', 'partial', 'failed', 'cancelled'}:
                job['ended_at'] = row['updated_at'].isoformat() + '+00:00'
            self.jobs[job['id']] = job
        self.active = next((job['id'] for job in self.jobs.values() if job['status'] == 'running'), None)

    def enqueue(self, job, request, spec):
        job['display_name'] = request.get('display_name') or job['key']
        if spec is None:
            if request['action'] == 'recover':
                self._recover(job, request.get('task'))
            else:
                self.queue.operate(job['key'], job, request['action'])
        else:
            self._create(job, spec)
        self.sync()
        return dict(job_id=job['id'], key=job['key'])

    def _recover(self, job, case_id):
        from .recovery import available, inspect
        row = self.records[job['key']]
        case = next((case for case in row['plan']['cases'] if case['id'] == case_id), None)
        task = next((task for task in self.queue.tasks(job['key']) if task['case_id'] == case_id), None)
        if not case or not task or not available(task, row['plan']['spec']['reviewer']):
            raise ValueError('请选择一个有保存证据的未完成服务端 Agent 案例，继续查询原任务')
        expected = inspect(self.store, task, case['safe_id'])
        self.queue.recover_agent(job['key'], job, case_id, expected)

    def _create(self, job, spec):
        from runner.cloud import checked_instances
        from webui.profiles import save_run_profile
        from local import ALLOWED
        if spec['suite'] in {'mcr', 'ccrab'} or spec['reviewer'] == 'cline_skill':
            raise ValueError('集群当前支持单轮 LCR、服务端 Agent 和 DSH；多轮/修复任务使用单实例部署')
        from .settings import runtime_key
        spec = dict(spec, case_jobs=1, cluster_runtime=runtime_key(spec['reviewer']))
        dataset = self.dataset_path(spec['dataset'])
        items = checked_instances(dataset, spec['suite'])
        if any(item.instance_id.startswith('__') for item in items):
            raise ValueError('案例标识不能使用集群内部任务前缀 __')
        cases = [dict(id=item.instance_id, repo=item.repo, safe_id=item.safe_id) for item in items]
        env = self.environment(job['profile'])
        profile = save_run_profile(self, {key: value for key, value in env.items() if key in ALLOWED})
        job['profile'] = profile
        job['resource_dataset'] = spec['dataset']
        # No model secrets are in the input manifest or SQL payload.
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / 'dataset.jsonl').write_bytes(dataset.read_bytes())
            _copy_skills(self, spec, target)
            record = self.store.capture(target)
        pool = self.cluster['pool']
        self.queue.create(job, spec, record, cases, pool, profile)

    def submit(self, request):
        self.sync()
        if request.get('action') == 'create' and request.get('name') in self.records:
            raise ValueError('批次名已存在')
        if request.get('action') != 'create':
            key = request.get('key')
            if key not in self.records:
                raise ValueError('集群只操作共享数据库中登记的批次')
            if request.get('retry') or (request.get('task') and request.get('action') != 'recover'):
                raise ValueError('集群仅支持服务端 Agent 的原任务查询恢复，不支持在原批次重新提交检视')
            row = self.records[key]
            job = dict(row['job'], id=uuid.uuid4().hex, action=request.get('action'),
                       status='queued', created_at=now())
            return self.enqueue(job, request, None)
        return super().submit(request)

    def describe(self, key):
        identifier(key)
        row = self.records[key]
        spec, job = row['plan']['spec'], row['job']
        result = row['result'] or row['prepared'] or {}
        value = dict(result.get('summary') or {})
        cases = _case_views(row, self.queue.tasks(key))
        _scored_cases(row, cases)
        value.update(key=key, name=key, display_name=job.get('display_name', key), suite=spec['suite'], reviewer=spec['reviewer'],
                     model=value.get('model', '—'), judge_model=value.get('judge_model', '—'),
                     evaluator=spec['evaluator'], created=job['created_at'], profile=job['profile'],
                     cases=list(cases.values()), total=len(cases), completed=sum(c['status']=='complete' for c in cases.values()),
                     cluster_status=row['status'], distributed=True)
        value.setdefault('status', 'pending')
        value.setdefault('metrics', {})
        value.setdefault('artifacts', [])
        if row['status'] in {'partial', 'failed', 'cancelled'}:
            value['status'] = 'partial_scored' if value['metrics'] else row['status']
        if row['status'] in {'reviewing', 'scoring'}:
            value['status'] = row['status']
        return value

    def snapshot(self, compact=False):
        self.sync()
        value = super().snapshot(compact=compact)
        value.update(queue_mode=True, cluster_mode=True, worker_ready=self.queue.meta('heartbeat') is not None,
                     recovery_required=False, storage_mode='persistent',
                     run_storage={'enabled':True, 'status':'ready', 'message':'MySQL 调度 / COS 逐次证据持久化'})
        return value

    def materialize(self, key):
        identifier(key)
        self.sync()
        row = self.records.get(key)
        if not row:
            raise ValueError('批次不存在')
        record = row['result'] or row['prepared']
        if not record or not record.get('snapshot'):
            raise ValueError('批次尚未生成可下载文件')
        destination = super().run_dir(key)
        with self.lock:
            marker = destination / '.cluster-snapshot'
            digest = record['snapshot']['sha256']
            if not marker.exists() or marker.read_text() != digest:
                self.store.restore(record['snapshot'], destination)
                marker.write_text(digest)
            if record.get('archive'):
                self.store.get(record['archive'], destination.parent / (key + '-results.tar.gz'))
        return destination

    def artifact(self, key, relative):
        self.materialize(key)
        return super().artifact(key, relative)

    def logs(self, job_id):
        self.sync()
        if job_id not in self.jobs:
            raise ValueError('任务不存在')
        key = self.jobs[job_id]['key']
        output = []
        for task in self.queue.tasks(key):
            record = task['result'] or task['checkpoint'] or {}
            output.append(task['case_id'] + '：' + task['status'])
            if record.get('log'):
                with tempfile.TemporaryDirectory() as temporary:
                    path = Path(temporary) / 'log'
                    self.store.get(record['log'], path)
                    output.append(path.read_text(encoding='utf-8', errors='replace')[-10000:])
        text = '\n'.join(output)
        return dict(text=self.redact(text[-200000:]), truncated=len(text)>200000)

    def stop(self, job_id, actor=None):
        self.sync()
        if job_id not in self.jobs:
            raise ValueError('任务不存在')
        self.queue.cancel_run(self.jobs[job_id]['key'])

    def acknowledge_recovery(self, actor):
        raise ValueError('集群按租约自动接手，无需恢复本地队列')

    def shutdown(self):
        from webui.case_import import stop
        stop(self)


def _copy_skills(app, spec, target):
    import shutil
    from webui.skill_assets import version_folder
    for version in spec.get('skill_versions', []):
        folder = version_folder(app, version)
        shutil.copytree(folder, target / 'skills' / version)


def _scored_cases(row, cases):
    if not row['result'] or not row['result'].get('summary'):
        return
    for case in row['result']['summary'].get('cases', []):
        if case['id'] in cases:
            # Old aggregate coverage remains frozen; it must not overwrite a new query's status.
            cases[case['id']].update({key: case[key] for key in ('scored', 'metrics') if key in case})


def _case_views(row, tasks):
    from .recovery import available, FINISHED
    cases = {case['id']: dict(case, status='pending', scored=False) for case in row['plan']['cases']}
    for task in tasks:
        if task['case_id'] not in cases:
            continue
        current = cases[task['case_id']]
        result_case = next((case for case in (task['result'] or {}).get('summary', {}).get('cases', [])
                            if case['id'] == task['case_id']), {})
        current.update(result_case)
        status = result_case.get('status', 'complete') if task['status'] == 'succeeded' else task['status']
        current.update(status=status, worker=task['owner'], attempts=task['generation'],
                       can_query_remote=row['status'] in FINISHED and available(task, row['plan']['spec']['reviewer']))
    return cases


def _status_message(status):
    return {'preparing':'等待兼容版本 Worker 创建批次', 'reviewing':'多个 Worker 正在检视案例',
            'scoring':'案例已结束，正在统一评分并生成报告', 'ready':'批次已创建，可开始检视',
            'succeeded':'任务完成，结果已保存到 COS', 'failed':'任务失败，已保存的证据保留',
            'partial':'部分完成；报告或评分仅覆盖已完成答卷，未完成案例可查看日志',
            'cancelling':'正在停止各实例任务', 'cancelled':'任务已停止'}.get(status, status)
