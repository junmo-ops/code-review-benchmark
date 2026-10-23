"""Public runner fixture: real Git, fixed DSH, local HTTP; no executor stubs."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


class Probe:
    def __init__(self, root, model, minutes, head_text, prepare_head=None, skill_version=None, assets_root=None,
                 instance_id='local.case', cli_entry=None):
        self.root = root
        self.run = root / 'runs/baseline'
        self.repo = root / 'repos/test__repo'
        self.repo.mkdir(parents=True)
        self.head_text = head_text
        self.prepare_head = prepare_head
        self.instance_id = instance_id
        self.cli_entry = cli_entry
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / 'backend/src'),
            LCR_LLM_MODEL='dsh-review', LCR_LLM_TOKEN='local-secret', LCR_LLM_BASE_URL=model.url,
            JUDGE_MODEL='dsh-judge', JUDGE_API_KEY='judge-secret', JUDGE_BASE_URL=model.url,
            BENCH_CASE_STORAGE_MODE='local', BENCH_RUN_STORAGE_MODE='local')
        if assets_root:
            self.env['BENCH_DATA_ROOT'] = str(assets_root)
        self.seed()
        selection = ['--skill-mode', 'skill', '--skill-version', skill_version] if skill_version else ['--skill-mode', 'baseline']
        result = self.stage('init', '--dataset', str(root / 'dataset.jsonl'), '--repos-root',
            str(root / 'repos'), '--model', 'dsh-review', '--reviewer', 'dsh_skill',
            *selection, '--timeout-minutes', str(minutes))
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args],
                                       stderr=subprocess.PIPE, text=True).strip()

    def seed(self):
        self.git('init', '-q')
        self.git('config', 'user.email', 'probe@example.invalid')
        self.git('config', 'user.name', 'DSH contract probe')
        (self.repo / 'sample.py').write_text('value = 1\n')
        (self.repo / 'AGENTS.md').write_text('REPOSITORY_INSTRUCTION_MUST_NOT_LOAD')
        (self.repo / '.env').write_text('BENCH_DSH_KEY=REPOSITORY_ENV_MUST_NOT_LOAD\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'base')
        base = self.git('rev-parse', 'HEAD')
        (self.repo / 'sample.py').write_text(self.head_text)
        if self.prepare_head:
            self.prepare_head(self.repo)
        self.git('add', '.')
        self.git('commit', '-qam', 'head')
        head = self.git('rev-parse', 'HEAD')
        row = dict(instance_id=self.instance_id, repo='test/repo', base_commit=base, head_commit=head,
            reference_comments=[dict(path='sample.py', start_line=1, end_line=1,
                text='REFERENCE_MUST_NOT_BE_VISIBLE', side='right')])
        (self.root / 'dataset.jsonl').write_text(json.dumps(row) + '\n')

    def stage(self, stage, *args, timeout=100):
        entry = [str(self.cli_entry)] if self.cli_entry else ['-m', 'runner.cloud']
        command = [str(ROOT / '.venv/bin/python'), *entry, stage,
            '--run-id', 'baseline', '--runs-root', str(self.root / 'runs'), *args]
        result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=timeout)
        (self.root / (stage + '.log')).write_text(result.stdout + result.stderr)
        return result

    def review(self):
        return self.stage('review', '--instance', self.instance_id, '--external-timeout-seconds', '700')


@contextmanager
def case(model, minutes=10, head_text='value = 2\n', prepare_head=None, **selection):
    with tempfile.TemporaryDirectory(prefix='dsh-contract-') as directory:
        probe = Probe(Path(directory), model, minutes, head_text, prepare_head, **selection)
        try:
            yield probe
        finally:
            destination = os.getenv('DSH_EVIDENCE_ROOT')
            if destination:
                shutil.copytree(probe.root, Path(destination) / str(time.time_ns()),
                                ignore=shutil.ignore_patterns('repos'))
