"""Extension routing; legacy evaluator bytes and frozen identities stay intact."""
import hashlib, subprocess, sys
from runner.storage import ROOT
from evaluators import registry as legacy

ENGINE = 'martian_suite_adapter_v2'


def identity():
    old = legacy.identity()
    files = dict(old['files'])
    files.update({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted((ROOT/'scoring').glob('*.py'))})
    return {'id': ENGINE, 'files': files}


def freeze(name, suite):
    if suite.id != 'codefuse_cr': return legacy.freeze(name, suite)
    if name != 'martian': raise ValueError('CodeFuse 目前只接入 Martian 裁判，未接入官方原始评分')
    return {'id':'martian', 'engine':ENGINE, 'identity':identity(),
            'parameters':dict(legacy.PARAMETERS)}


def verify(config):
    cfg = config.get('evaluator', {})
    if 'engine' not in cfg: return legacy.verify(config)
    if cfg['engine'] != ENGINE or cfg.get('id') != 'martian': raise ValueError('未知裁判引擎')
    if cfg['identity'] != identity() or cfg['parameters'] != legacy.PARAMETERS:
        raise ValueError('裁判适配代码或参数改变，请新建评分批次')
    if config['suite']['id'] != 'codefuse_cr': raise ValueError('裁判引擎与案例集不匹配')
    return 'martian'


def execute(stage, suite, cases, run, config):
    if 'engine' not in config.get('evaluator', {}):
        return legacy.execute(stage, suite, cases, run, config)
    verify(config)
    subprocess.run([sys.executable,'-m','scoring.martian',stage,'--run',str(run)],cwd=ROOT,check=True)
