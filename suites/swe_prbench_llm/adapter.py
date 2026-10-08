"""Internal LLM-only protocol, isolated from frozen historical SWE scoring."""
import hashlib
import subprocess
import sys
from runner.storage import ROOT
from suites.swe_prbench.adapter import SWEPRBenchSuite

class SWELLMSuite(SWEPRBenchSuite):
    id = 'swe_prbench_llm'
    name = 'SWE-PRBench（内部大模型裁判）'
    parameters = {'eval_rounds': 1, 'protocol': 'internal_llm_only_v1',
                  'semantic_backend': 'llm_only', 'temperature': 0,
                  'max_tokens': 8192, 'matching': 'judge_confirmed_one_to_one',
                  'official_leaderboard_comparable': False}

    def identity(self):
        identity = super().identity()
        identity['files'].update({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT/'suites/swe_prbench_llm').glob('*.py'))})
        return identity

    def _execute(self, stage, run):
        subprocess.run([sys.executable,'-m','suites.swe_prbench_llm',stage,'--run',str(run)],cwd=ROOT,check=True)
