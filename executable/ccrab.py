"""c-CRAB one-case preparation. No model calls or container mutations in this entrypoint."""
from runner.runtime import data_root, config_root, docker_context

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from runner.contracts import ReviewCase
from runner.storage import ROOT, save, validate_answer

SOURCE = ROOT / 'data/sources/ccrab'
UPSTREAM = ROOT / 'history/ccrab-upstream'
IMAGE = 'ccrab-networkx-pilot:local'
CONTEXT = docker_context()


def load():
    manifest = json.loads((SOURCE / 'provenance.json').read_text(encoding='utf-8'))
    for name, expected in manifest['files'].items():
        path = ROOT / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('案例或测试文件发生变化：' + name)
    row = json.loads((SOURCE / 'pilot1.raw.json').read_text(encoding='utf-8'))
    tests = json.loads((SOURCE / 'testgen' / row['instance_id'] / 'result.json').read_text(encoding='utf-8'))
    if not tests['results'] or not all(t['success'] and not t['before_passed'] and t['after_passed'] for t in tests['results']):
        raise ValueError('所选发布测试未通过上游前后版本验证')
    return row, manifest


def review_input(row):
    # Do not pass problem_statement, reference comments, merged patch, or tests to a reviewer.
    return {key: row[key] for key in ('instance_id', 'repo', 'base_commit')} | {
        'head_commit': row['commit_to_review']['head_commit'],
        'patch_to_review': row['commit_to_review']['patch_to_review'],
    }


def findings(answer, row):
    if answer.get('repo') != row['repo']:
        raise ValueError('答卷仓库不匹配')
    result = []
    for comment in answer['review']['comments']:
        path = comment.get('path')
        if not isinstance(path, str) or not path or path.startswith(('/', '\\')) or '..' in Path(path).parts or ':' in path:
            raise ValueError('意见文件路径无效')
        if not isinstance(comment.get('content'), str) or not comment['content'].strip():
            raise ValueError('意见缺少正文')
        start, end = comment.get('start_line'), comment.get('end_line')
        if type(start) is not int or type(end) is not int or start < 1 or end < start:
            raise ValueError('意见行号无效')
        result.append(dict(file=path, start_line=start, end_line=end,
                           issue_header='LCR finding', issue_content=comment['content']))
    return {'tools': {'lcr': {'success': True, 'findings': result}}}


def inspect(command):
    try:
        p = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=20)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def check():
    row, manifest = load()
    case = review_input(row)
    repo = data_root(ROOT) / 'repos/networkx__networkx'
    docker = shutil.which('docker')
    daemon = bool(docker) and inspect([docker, '--context', CONTEXT, 'info'])
    image = daemon and inspect([docker, '--context', CONTEXT, 'image', 'inspect', IMAGE])
    history = all(inspect(['git', '-C', str(repo), 'cat-file', '-e', case[k]+'^{commit}']) for k in ('base_commit', 'head_commit'))
    patch_matches = False
    if history:
        from suites.codefuse_cr.adapter import normalize_patch
        diff = subprocess.run(['git', '-C', str(repo), 'diff', '--no-ext-diff', '--no-textconv', '--no-color', case['base_commit']+'...'+case['head_commit'], '--'], capture_output=True, text=True, encoding='utf-8', timeout=20)
        patch_matches = diff.returncode == 0 and normalize_patch(diff.stdout) == normalize_patch(case['patch_to_review'])
    upstream = False
    if (UPSTREAM / 'UPSTREAM.json').exists():
        identity = json.loads((UPSTREAM / 'UPSTREAM.json').read_text(encoding='utf-8'))
        upstream = all((UPSTREAM / p).exists() and hashlib.sha256((UPSTREAM / p).read_bytes()).hexdigest() == digest for p, digest in identity['files'].items())
    checks = {'案例与测试哈希': True, '官方执行源码': upstream, '本地案例仓库提交': history, '待检视 diff 与发布 patch 一致': patch_matches,
              'Docker 命令': bool(docker), 'Docker 服务': daemon, '案例镜像': image}
    blockers = [name for name, ok in checks.items() if not ok]
    checks['DeepSeek 修复适配器'] = (ROOT/'executable/deepseek_repair.py').exists()
    if not checks['DeepSeek 修复适配器']: blockers.append('DeepSeek 修复适配器')
    result = {'status': 'blocked' if blockers else 'ready_not_executed', 'instance_id': row['instance_id'],
              'upstream_revision': manifest['revision'], 'checks': checks, 'blockers': blockers,
              'review_executed': False, 'repair_executed': False, 'tests_executed': False,
              'score': None, 'local_adapted_image_tag': IMAGE}
    folder = data_root(ROOT) / 'runs/ccrab/preflight'
    save(folder/'readiness.json', result)
    report = '# c-CRAB 单例接入准备\n\n状态：准备中，尚未执行检视、修复或容器测试；没有测评分数。\n\n'
    report += f"案例：`{row['instance_id']}`\n\n"
    report += '\n'.join('- '+name+'：'+('就绪' if ok else '未就绪') for name, ok in checks.items())
    report += '\n\n待完成：\n'+ '\n'.join('- '+name for name in blockers)
    report += '\n\n上游测试成功记录只作为选例依据，不是本机验证结果。\n'
    (folder/'report.zh-CN.md').write_text(report, encoding='utf-8')
    print(report)
    print('检查记录：', folder/'readiness.json')
    return 2 if blockers else 0


def main():
    parser = argparse.ArgumentParser(description='c-CRAB 单例准备；不会调用模型或启动容器')
    subs = parser.add_subparsers(dest='stage', required=True)
    subs.add_parser('check', help='验证素材、仓库和容器条件，并写入检查报告')
    subs.add_parser('plan', help='显示已固定案例与尚未完成的执行阶段')
    adapter = subs.add_parser('adapt', help='将范围匹配的完整答卷转换为上游 findings 文件')
    adapter.add_argument('--answer', type=Path, required=True)
    adapter.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        row, manifest = load()
        if args.stage == 'check':
            return check()
        if args.stage == 'plan':
            print(json.dumps({'case': review_input(row), 'status': 'prepared_not_executed',
                'stages': ['容器与镜像准备', '原版本测试失败/修复版本测试通过的对照验证',
                           'LCR 检视', '完整答卷转换', '固定修复 Agent 按 LCR 意见修改', '执行发布测试并生成报告'],
                'repair_backend': manifest['repair_backend']}, ensure_ascii=False, indent=2))
            return 0
        case = ReviewCase(row['instance_id'], row['repo'], row['base_commit'], row['commit_to_review']['head_commit'])
        answer = validate_answer(case, args.answer)
        payload = findings(answer, row)
        target = args.output / case.instance_id / 'result.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('x', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print('已转换：', target)
        if not payload['tools']['lcr']['findings']:
            print('完整空答卷已保留；上游脚本会跳过空意见，不将它报告为执行通过。')
        return 0
    except (ValueError, OSError, KeyError, TypeError) as e:
        print('准备失败：'+str(e), file=sys.stderr)
        return 1
