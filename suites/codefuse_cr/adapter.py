"""SWE-CARE/CodeFuse official records; repository inputs never include answers."""
import hashlib, json, random, re, subprocess
from pathlib import Path
from runner.contracts import ReviewCase
from runner.storage import ROOT


def records(path):
    with Path(path).open(encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    seen = set()
    for row in rows:
        name = row.get("instance_id", "")
        if not re.fullmatch(r"[A-Za-z0-9_.@-]+", name) or name in (".", "..") or name in seen:
            raise ValueError("CodeFuse 案例 ID 无效或重复")
        seen.add(name)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", row.get("repo", "")) or '..' in row['repo'].split('/'):
            raise ValueError("CodeFuse 仓库无效")
        commit = row.get("commit_to_review", {})
        for sha in (row.get("base_commit", ""), commit.get("head_commit", "")):
            if not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise ValueError("CodeFuse 必须提供 base 与待检视 head 的完整 SHA")
        if not isinstance(commit.get("patch_to_review"), str) or not commit['patch_to_review'].startswith('diff --git '):
            raise ValueError("CodeFuse 缺少待检视 patch")
        comments = row.get("reference_review_comments")
        if not isinstance(comments, list) or not comments or any(not isinstance(c,dict) or not isinstance(c.get('text'),str) or not c['text'].strip() for c in comments):
            raise ValueError("CodeFuse 参考意见为空或格式无效")
    if not rows: raise ValueError("CodeFuse 数据为空")
    return rows


def normalize_patch(patch):
    # Git versions/attributes differ in index abbreviations and hunk function labels.
    lines=[]
    for line in patch.split('\n'):
        if line.startswith('index '):continue
        if line.startswith('@@ '):line=re.sub(r'^(@@ .*? @@).*$',r'\1',line)
        lines.append(line)
    return '\n'.join(lines).rstrip('\n')


class CodeFuseSuite:
    id = "codefuse_cr"
    name = "CodeFuse-CR-Bench / SWE-CARE（辅测）"
    version = "1"
    parameters = {"protocol": "repository_review_martian_v1", "background": "code_only",
                  "official_leaderboard_comparable": False}
    required_capabilities = frozenset({"git_range", "findings"})
    supported_evaluators = frozenset({"martian"})

    def load_cases(self, dataset):
        return [ReviewCase(row['instance_id'], row['repo'], row['base_commit'],
                           row['commit_to_review']['head_commit']) for row in records(dataset)]

    def reference_comments(self, row):
        return [{'comment':c['text'], 'path':c.get('path'), 'line':c.get('line')}
                for c in row['reference_review_comments']]

    def check_repository(self, case, repo, dataset):
        row=next(r for r in records(dataset) if r['instance_id']==case.instance_id)
        result=subprocess.run(['git','-C',str(repo),'diff','--no-ext-diff','--no-textconv',
                               '--no-color',case.base_commit+'...'+case.head_commit,'--'],
                              capture_output=True,text=True,encoding='utf-8',check=True)
        if normalize_patch(result.stdout)!=normalize_patch(row['commit_to_review']['patch_to_review']):
            raise ValueError('CodeFuse 实际 merge-base diff 与官方待检视 patch 不一致，停止检视')

    def identity(self):
        paths = sorted((ROOT/'suites/codefuse_cr').glob('*.py'))
        return {'id':self.id, 'adapter_version':self.version,
                'files':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}

    def provenance(self, dataset):
        sha = hashlib.sha256(Path(dataset).read_bytes()).hexdigest()
        p = ROOT/'data/sources/codefuse_cr/provenance.json'
        if p.exists():
            manifest = json.loads(p.read_text(encoding='utf-8'))
            if sha == manifest['dataset_sha256']:
                for name, expected in manifest['files'].items():
                    if hashlib.sha256((p.parent/name).read_bytes()).hexdigest() != expected:
                        raise ValueError('CodeFuse 来源文件哈希变化：'+name)
                return {'status':'pinned_official_source', 'manifest':manifest}
        return {'status':'unverified_custom_dataset', 'dataset_sha256':sha}

    def evaluate(self, cases, run, config):
        raise ValueError('CodeFuse 尚未接入官方原始评分，请使用 --evaluator martian')

    def report(self, run, config):
        raise ValueError('CodeFuse 请使用 Martian 报告')

    def convert(self, source, output, limit=None, seed=42):
        rows = records(source)
        if limit is not None:
            if limit < 1: raise ValueError('limit 必须为正整数')
            rows = random.Random(seed).sample(rows, min(limit,len(rows)))
        target = Path(output)
        if target.exists(): raise ValueError('输出已存在，拒绝覆盖')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(''.join(json.dumps(row,ensure_ascii=True)+'\n' for row in rows),encoding='utf-8')
