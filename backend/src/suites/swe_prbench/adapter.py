"""SWE-PRBench native data, separate scoring; LCR repository protocol variant."""
import hashlib
import importlib.metadata
import json
import re
import subprocess
import sys
from pathlib import Path
from runner.contracts import ReviewCase
from runner.storage import ROOT


def records(path):
    rows = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
    for row in rows:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', row['task_id']):
            raise ValueError('无效的 SWE-PRBench task_id')
        for key in ('base_commit', 'head_commit'):
            if not re.fullmatch(r'[0-9a-f]{40}', row[key]):
                raise ValueError(f'{key} 必须为完整 SHA')
        if not isinstance(row.get('annotation', {}).get('comments'), list) or not row.get('diff_patch'):
            raise ValueError('需要官方 diff_patch 与 annotation.comments；不能套用 AACR 标注')
        if not row.get('contexts', {}).get('A', {}).get('rendered'):
            raise ValueError('必须保留官方冻结上下文 A 作为来源证据')
    return rows


class SWEPRBenchSuite:
    id = 'swe_prbench'
    name = 'SWE-PRBench（LCR 仓库适配）'
    version = '1'
    required_capabilities = frozenset({'git_range', 'findings'})
    parameters = {'eval_rounds': 1, 'protocol': 'lcr_repository_variant',
                  'semantic_backend': 'upstream_lexical_fallback',
                  'official_leaderboard_comparable': False}

    def load_cases(self, dataset):
        return [ReviewCase(r['task_id'], r['repo'], r['base_commit'], r['head_commit']) for r in records(dataset)]

    def identity(self):
        paths = [p for d in ('backend/src/suites/swe_prbench', 'vendor/swe_prbench')
                 for p in (ROOT/d).rglob('*') if p.is_file() and '__pycache__' not in p.parts
                 and (p.suffix == '.py' or p.name in ('UPSTREAM.json','LICENSE'))]
        return {'id': self.id, 'name': self.name, 'adapter_version': self.version,
                'files': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)},
                'dependencies': {n: importlib.metadata.version(n) for n in ('numpy','requests','PyYAML','openai')}}

    def provenance(self, dataset):
        path = ROOT/'data/sources/swe_prbench/provenance.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        sha = hashlib.sha256(Path(dataset).read_bytes()).hexdigest()
        if sha != manifest['dataset_sha256']:
            return {'status':'unverified_custom_dataset', 'dataset_sha256':sha}
        for name, expected in manifest['files'].items():
            if hashlib.sha256((path.parent/name).read_bytes()).hexdigest() != expected:
                raise ValueError('SWE-PRBench 原始资源哈希不一致：'+name)
        return {'status':'pinned_official_source', 'manifest':manifest}

    def _execute(self, stage, run):
        subprocess.run([sys.executable,'-m','suites.swe_prbench',stage,'--run',str(run)],cwd=ROOT,check=True)

    def evaluate(self, cases, run, config):
        self._execute('eval',run)

    def report(self, run, config):
        self._execute('report',run)

    def convert(self, source, output, limit=None, seed=42):
        import random
        rows = records(source)
        if limit is not None:
            if limit < 1: raise ValueError('limit 必须大于 0')
            rows = random.Random(seed).sample(rows, min(limit,len(rows)))
        Path(output).parent.mkdir(parents=True,exist_ok=True)
        Path(output).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
