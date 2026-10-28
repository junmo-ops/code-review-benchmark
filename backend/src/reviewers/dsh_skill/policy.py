"""Compose a closed tool surface over the exact shipped headless profile."""
import json
import yaml
from . import runtime

ALLOWED_ROWS = frozenset('''timer llm session typert typert-loader
typert-gateway remote-access agent agent-default-model llm-pi-ai session-persistence-jsonl
session-query-sqlite working-directory session-projection storage storage-json storage-domain
session-projection-cache subprocess sandbox sandbox-policy approval shell-env
fs-observation-policy tool-fs tool-fs-search token-meter compaction-basic llm-retry
timeout-policy spill-local spill-policy session-checkpoint-policy tool-result-pruner
tools system-prompt agent-loop fs-sandbox'''.split())


class BundleLoader(yaml.SafeLoader):
    pass


BundleLoader.add_constructor('tag:yaml.org,2002:js', lambda loader, node: loader.construct_scalar(node))


def overlay(model, skill=None, work=None, max_output_tokens=8192):
    source = runtime.MODULES / '@deepseek-ai/dsh-base/cordis.patch.yml'
    bundle = yaml.load(source.read_text(), Loader=BundleLoader)
    rows = bundle[0]['insert']
    allowed = ALLOWED_ROWS | ({'skill', 'tool-skill'} if skill else set())
    paths = runtime.locations(work)
    patches = [dict(id=row['id'], disabled=True) for row in rows if row['id'] not in allowed]
    settings = {
        'tool-fs': dict(read=True, readImage=False, write=False, edit=False),
        'agent-default-model': dict(provider='benchmark', model=model),
        'approval': dict(policy='never'),
        'session-persistence-jsonl': dict(root=paths['home'] + '/sessions', compression='none'),
        'sandbox-policy': dict(mode='read-only', workspaceRoot=paths['repo']),
        'compaction-basic': dict(compactionRetries=0, maxOverflowRetries=0),
        'llm-pi-ai': dict(providers={'benchmark': dict(
            api='openai-completions', baseURL='http://127.0.0.1:8123/v1',
            apiKeyEnv='BENCH_DSH_KEY', models=[dict(id=model, contextWindow=131072,
                                                 maxTokens=runtime.output_limit(max_output_tokens))],
            retryPolicy=dict(mode='normal', maxRetries=0))}),
    }
    patches += [dict(id=name, config=config) for name, config in settings.items()]
    patches += [dict(id='compaction-basic', disabled=True),
                dict(insert=[dict(id='benchmark-compaction', name=runtime.script('compaction.mjs'),
                                  config=dict(compactionRetries=0, maxOverflowRetries=0))]),
                dict(insert=[dict(id='benchmark-tool-scope', name=runtime.script('scope.mjs'), config=paths)]),
                dict(id='headless-runner', inject=['headlessStartup', 'compaction', 'benchmarkScope'])]
    if skill:
        patches += [dict(insert=[dict(id='benchmark-skill', name=runtime.script('candidate.mjs'),
                                    config=dict(skill, root=paths['skill']))]),
                    dict(id='headless-runner', inject=['headlessStartup', 'compaction', 'benchmarkScope', 'benchmarkSkill'])]
    return json.dumps(patches)
