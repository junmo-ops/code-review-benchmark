"""Company report API; fixed service model, direct ranges and persisted report IDs."""
from . import repositories
from pathlib import PurePosixPath
import json
import subprocess
from urllib.parse import urlsplit

CONTRACT = 'company-repo-review-v1'
MODEL = 'DeepSeek（具体版本未确认）'
STATES = {'PROCESSING': 'running', 'SUCCESS': 'succeeded', 'FAIL': 'failed'}


def validate(config):
    allowed = {'contract', 'base_url', 'repositories', 'poll_seconds', 'request_timeout_seconds'}
    if set(config) - allowed:
        raise ValueError('公司 Agent 配置包含未知字段')
    url = urlsplit(config.get('base_url', ''))
    if url.scheme not in {'http', 'https'} or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ValueError('Agent base_url 无效')
    repositories.validate(config)
    validate_limits(config)
    return dict(config, simulation=False, diff_mode='direct', paths={
        'create': '/repo-review/async-report',
        'status': '/review-report-scan/report/{task_id}',
        'result': '/review-report-scan/report/{task_id}'})


def validate_limits(config):
    for key, default in [('poll_seconds', 3), ('request_timeout_seconds', 20)]:
        value = config.get(key, default)
        if type(value) not in (int, float) or not 0.01 <= value <= 60:
            raise ValueError('轮询间隔或请求超时无效')


def payload(item, repo, config, run):
    record_path = run / 'raw' / (item.safe_id + '.remote.json')
    if record_path.exists():
        return saved_request(record_path, item, config)
    if config.get('diff_mode', 'merge_base') == 'direct':
        return {'repo_name': repositories.resolve(config, item.repo),
                'commit_ids': [item.base_commit, item.head_commit], 'review_mode': 'from_to'}
    if repo is None:
        raise ValueError('公司 Agent 需要本地仓库计算共同祖先')
    result = subprocess.run(['git', '-C', str(repo), 'merge-base', '--all', item.base_commit, item.head_commit],
                            capture_output=True, timeout=60)
    bases = result.stdout.decode().split()
    if result.returncode or len(bases) != 1:
        raise ValueError('无法确定唯一共同祖先；请准备包含案例提交的本地仓库')
    return {'repo_name': repositories.resolve(config, item.repo), 'commit_ids': [bases[0], item.head_commit],
            'review_mode': 'from_to'}


def saved_request(path, item, config):
    value = json.loads(path.read_text())['request']
    commits = value.get('commit_ids')
    if not isinstance(commits, list) or len(commits) != 2 or commits[1] != item.head_commit:
        raise ValueError('保存的请求提交范围无效')
    if value.get('repo_name') != repositories.resolve(config, item.repo) or value.get('review_mode') != 'from_to':
        raise ValueError('保存的请求仓库或方式不一致')
    return value


def normalize(data, kind, task_id):
    if data.get('returnCode') != 'SUC0000':
        raise ValueError('公司 Agent 业务请求失败：' + str(data.get('returnCode', '缺少业务码')))
    body = data.get('body')
    if not isinstance(body, dict):
        raise ValueError('公司 Agent 响应缺少 body')
    if kind == 'create':
        identifier = body.get('report_id')
        if type(identifier) is not int or identifier <= 0:
            raise ValueError('公司 Agent 未返回有效 report_id')
        return {'task_id': str(identifier), 'response': data}
    report = body.get('report')
    if not isinstance(report, dict):
        raise ValueError('报告不存在或响应无效；保留原 report_id，不重新创建')
    if type(report.get('id')) is not int or str(report['id']) != task_id:
        raise ValueError('返回报告 ID 与原任务不一致')
    if report.get('status') not in STATES:
        raise ValueError('报告状态未知：' + str(report.get('status')))
    return {'status': STATES[report['status']], 'report': report, 'response': data, 'error': extra_info(report).get('error')}


def extra_info(report):
    value = report.get('extra_info')
    return value if isinstance(value, dict) else {}


def token_usage(report):
    raw = extra_info(report).get('token_usage')
    if not isinstance(raw, dict):
        return {}
    fields = {'total_input_tokens': 'input_tokens', 'total_output_tokens': 'output_tokens',
              'total_tokens': 'total_tokens'}
    usage = {target: raw[source] for source, target in fields.items()
             if type(raw.get(source)) is int and raw[source] >= 0}
    # The service also emits all-zero counters when usage metadata is absent.
    return usage if any(usage.values()) else {}


def comment(issue):
    if not isinstance(issue, dict):
        raise ValueError('问题格式无效')
    path = issue.get('file_path')
    if not isinstance(path, str) or not path or path.startswith(('/', '\\')) or '\\' in path or ':' in path or '..' in PurePosixPath(path).parts:
        raise ValueError('问题文件路径无效')
    start = issue.get('start_line')
    if start is None:
        start = issue.get('line_number')
    end = issue.get('end_line')
    if end is None:
        end = start
    return position_and_text(issue, path, start, end)


def position_and_text(issue, path, start, end):
    text = issue.get('description')
    if type(start) is not int or type(end) is not int or start < 1 or end < start:
        raise ValueError('问题缺少有效行号，保留原始报告，不生成不完整答卷')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('问题缺少描述，保留原始报告')
    suggestion = issue.get('fix_suggestion')
    if isinstance(suggestion, str) and suggestion.strip():
        text += '\n修复建议：' + suggestion
    return dict(path=path, start_line=start, end_line=end, content=text)


def convert(item, data, task_id, run):
    request = json.loads((run / 'raw' / (item.safe_id + '.remote.json')).read_text())['request']
    report = data['report']
    if data['status'] != 'succeeded' or str(report['id']) != task_id:
        raise ValueError('报告未成功或任务不匹配')
    expected = (request['repo_name'], *request['commit_ids'], 'from_to')
    actual = (report.get('full_name'), report.get('start_commit_sha'), report.get('end_commit_sha'), str(report.get('scan_type', '')).lower())
    if actual != expected:
        raise ValueError('报告仓库、范围或检视方式与提交请求不一致')
    issues = report.get('issues')
    if not isinstance(issues, list):
        raise ValueError('报告缺少完整 issues 数组')
    comments = [comment(issue) for issue in issues]
    return dict(instance_id=item.instance_id, repo=item.repo, base_commit=item.base_commit,
                head_commit=item.head_commit, reviewer='server_agent',
                review=dict(status='complete', comments=comments, summary=token_usage(report), llm={'model': MODEL}),
                service=dict(task_id=task_id, scope=request, simulation=False, engine_version=None,
                             model_source='operator_confirmed_family_only', usage=extra_info(report).get('token_usage'),
                             completeness_source='service_SUCCESS_contract',
                             timing={'trigger_at': report.get('trigger_at'), 'completed_at': report.get('completed_at')}))


def freeze(config, cfg):
    if config['contract'] == CONTRACT:
        cfg.update(review_model=MODEL, requested_model_only=False,
                   service_model_source='operator_confirmed_family_only', service_engine_version=None)
