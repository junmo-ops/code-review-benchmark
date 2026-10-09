"""Create comparison jobs using the same local/server job lifecycle."""
import hashlib
import json
import uuid
from pathlib import Path
from runner.storage import save
from suites.registry import get_suite
from reviewers.registry import get_reviewer
from webui.profiles import compose_roles, save_run_profile
from webui.service import NAME, now
from .scorer import PROTOCOL, PARAMETERS, identity
from .report import build


def create(app, data):
    if not isinstance(data,dict):raise ValueError('请求必须是对象')
    name=data.get('name');suite_id=data.get('suite');dataset=data.get('dataset');models=data.get('models')
    if not isinstance(name,str) or not NAME.fullmatch(name) or len(name)>65:raise ValueError('实验编号须为 1–65 位字母、数字或短横线')
    catalog=next((s for s in app.catalog() if s['id']==suite_id),None)
    if suite_id not in ('aacr','swe_prbench_llm','codefuse_cr') or not catalog or dataset not in {d['id'] for d in catalog['datasets']}:raise ValueError('请选择支持的单轮案例集')
    if not isinstance(models,list) or not 2<=len(models)<=6 or any(not isinstance(m,str) for m in models) or len(set(models))!=len(models):raise ValueError('请选择 2–6 个不同模型配置')
    timeout=data.get('timeout_minutes',10)
    if type(timeout) is not int or not 1<=timeout<=120:raise ValueError('案例时限须为 1–120 分钟')
    configs=[compose_roles(app,m,data.get('judge_profile')) for m in models]
    signatures=[(c['LCR_LLM_MODEL'],c['LCR_LLM_BASE_URL']) for c in configs]
    if len(set(signatures))!=len(signatures):raise ValueError('所选配置包含相同模型与接口，不能作为两个对照组')
    profiles={p['id']:p for p in app.profiles()};suite=get_suite(suite_id)
    source=app.dataset_path(dataset);cases=suite.load_cases(source)
    if not cases or len({c.instance_id for c in cases})!=len(cases):raise ValueError('案例为空或 ID 重复')
    key='compare-'+name;folder=app.run_dir(key)
    with app.lock:
        if app.active and not getattr(app,'queue_mode',False):raise ValueError('已有任务执行中，请完成后创建对比实验')
        if folder.exists():raise ValueError('实验编号已存在，请换一个编号')
        group_keys=[key+f'-g{i+1}' for i in range(len(models))]
        if any(app.run_dir(k).exists() for k in group_keys):raise ValueError('分组批次名称已存在')
        folder.mkdir(parents=True);saved=[]
        try:
            groups=[]
            for model,config,group_key in zip(models,configs,group_keys):
                profile=save_run_profile(app,config);saved.append(profile)
                groups.append(dict(key=group_key,label=profiles[model]['label'],model=config['LCR_LLM_MODEL'],profile=profile,
                    config_sha256=hashlib.sha256((app.configs/profile).read_bytes()).hexdigest()))
            manifest=dict(name=name,key=key,created_at=now(),suite=suite_id,dataset=dataset,
                dataset_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),case_ids=[c.instance_id for c in cases],
                judge_model=configs[0]['JUDGE_MODEL'],groups=groups,protocol=PROTOCOL,parameters=PARAMETERS,
                code_identity=identity(),suite_identity=suite.identity(),tool_identity=get_reviewer('lcr').tool_identity(),
                timeout_minutes=timeout,case_timeout_seconds=timeout*60,judge_case_timeout_seconds=600)
            save(folder/'experiment.json',manifest);(folder/'dataset.jsonl').write_bytes(source.read_bytes());build(folder)
            job_id=uuid.uuid4().hex
            job=dict(id=job_id,key=key,action='comparison',profile=groups[0]['profile'],status='queued',created_at=now(),step='',completed_steps=0,total_steps=1,message='等待执行',exit_code=None)
            app.enqueue(job,{'action':'comparison','key':key,'profile':groups[0]['profile'],'_actor':data.get('_actor','local')},None)
            return dict(key=key,job_id=job_id)
        except Exception:
            # Preserve any manifest evidence; never overwrite a possibly started job.
            if not (folder/'experiment.json').exists():
                for p in saved:(app.configs/p).unlink(missing_ok=True)
            raise


def list_experiments(app):
    values=[]
    for p in sorted((app.data/'runs').glob('compare-*/experiment.json')):
        if p.is_symlink() or p.parent.is_symlink():continue
        try:
            m=json.loads(p.read_text());report=json.loads((p.parent/'comparison.json').read_text())
            job=next(iter(sorted((j for j in app.jobs.values() if j['key']==m['key']),key=lambda j:j['created_at'],reverse=True)),None)
            values.append(dict(key=m['key'],name=m['name'],suite=m['suite'],judge_model=m['judge_model'],created_at=m['created_at'],
                               job_id=job['id'] if job else None,status=job['status'] if job else 'interrupted',report=report))
        except (OSError,ValueError,KeyError):continue
    return sorted(values,key=lambda v:v['created_at'],reverse=True)
