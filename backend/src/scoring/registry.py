"""Extension routing; legacy evaluator bytes and frozen identities stay intact."""
import hashlib, subprocess, sys
from runner.storage import ROOT
from evaluators import registry as legacy

ENGINE = 'martian_suite_adapter_v2'

MARTIAN_PARTIAL_SCOPE_REVISION = {
    'previous': {
        'cd77c57b5b0de382d4e1b35e57d8b0ad72115315862d8ee7469ed1ffce8583d4',
        'bd7bd9c5ee1f9f3b6f477d2bd85d65111594bd6a20cdd60c9025ff80c2890aa1',
    },
    'current': '6b2fd75b027545e9e25c8fcdb3237f008cd30e18a6db774e917629f10d98001c',
}


def identity():
    old = legacy.identity()
    files = dict(old['files'])
    files.update({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted((ROOT/'backend/src/scoring').glob('*.py'))})
    return {'id': ENGINE, 'files': files}


def freeze(name, suite):
    if suite.id != 'codefuse_cr': return legacy.freeze(name, suite)
    if name != 'martian': raise ValueError('CodeFuse 目前只接入 Martian 裁判，未接入官方原始评分')
    return {'id':'martian', 'engine':ENGINE, 'identity':identity(),
            'parameters':dict(legacy.PARAMETERS)}


def verify(config):
    cfg = config.get('evaluator', {})
    if 'engine' not in cfg:
        frozen_identity = cfg.get('identity', {})
        current = legacy.identity()
        old_files = frozen_identity.get('files', {})
        new_files = current.get('files', {})
        path = 'backend/src/evaluators/martian.py'
        if (cfg.get('id') == 'martian' and frozen_identity.get('id') == current['id']
                and set(old_files) == set(new_files)
                and all(old_files[name] == new_files[name] for name in old_files if name != path)
                and old_files.get(path) in MARTIAN_PARTIAL_SCOPE_REVISION['previous']
                and new_files.get(path) == MARTIAN_PARTIAL_SCOPE_REVISION['current']):
            config = dict(config)
            config['evaluator'] = dict(cfg, identity=current)
        return legacy.verify(config)
    if cfg['engine'] != ENGINE or cfg.get('id') != 'martian': raise ValueError('未知裁判引擎')
    if cfg['identity'] != identity() or cfg['parameters'] != legacy.PARAMETERS:
        raise ValueError('裁判适配代码或参数改变，请新建评分批次')
    if config['suite']['id'] != 'codefuse_cr': raise ValueError('裁判引擎与案例集不匹配')
    return 'martian'


def execute(stage, suite, cases, run, config):
    name = verify(config)
    if 'engine' not in config.get('evaluator', {}):
        if name == 'original':
            if stage == 'eval':
                return suite.evaluate(cases, run, config)
            return suite.report(run, config)
        return subprocess.run([sys.executable,'-m','evaluators.martian',stage,'--run',str(run)],cwd=ROOT,check=True)
    subprocess.run([sys.executable,'-m','scoring.martian',stage,'--run',str(run)],cwd=ROOT,check=True)
