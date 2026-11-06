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

# Skill mode receives the file-write and command-execution surface its policy
# documents (write/issues.json, bash for prepare/write scripts). Baseline mode
# stays strictly read-only inspection.
SKILL_EXTRA_ROWS = frozenset({'bash-sandbox', 'tool-bash'})

# pi-ai 默认 300000ms（5 分钟）的流空闲上限对长上下文收尾偏紧：32K 上下文实测
# 单请求可达约 310 秒，偶发停顿会直接撞上限被平台判超时，且重放显示不可稳定复现。
# 这里放宽到 10 分钟作为吸收偶发停顿的余量；总时长仍受批次 timeout_minutes 控制。
STREAM_IDLE_TIMEOUT_MS = 600000


class BundleLoader(yaml.SafeLoader):
    pass


BundleLoader.add_constructor('tag:yaml.org,2002:js', lambda loader, node: loader.construct_scalar(node))


def overlay(model, skill=None, work=None, max_output_tokens=8192):
    source = runtime.MODULES / '@deepseek-ai/dsh-base/cordis.patch.yml'
    bundle = yaml.load(source.read_text(encoding='utf-8'), Loader=BundleLoader)
    rows = bundle[0]['insert']
    allowed = ALLOWED_ROWS | ({'skill', 'tool-skill'} if skill else set())
    if skill:
        allowed |= SKILL_EXTRA_ROWS
    paths = runtime.locations(work)
    mode = runtime.runtime_mode()
    # POD mode shares the host process tree without an OS confinement backend
    # (no bubblewrap/Landlock/ACL runner); requesting a confined mode there
    # makes tool-bash fail with SandboxUnavailableError ("no sandbox backend
    # is usable on this host") and forces script-driven Skills to declare
    # unsupported. Skill mode in POD therefore falls back to the unfenced mode
    # and relies on the software guard (scope.mjs), which still confines
    # write/edit to the case snapshot and read/glob/grep to case+input+skill;
    # sandbox mode keeps the real bwrap confinement.
    sandbox_mode = ('danger-full-access' if (mode == 'pod' and skill)
                    else ('workspace-write' if skill else 'read-only'))
    patches = [dict(id=row['id'], disabled=True) for row in rows if row['id'] not in allowed]
    settings = {
        # Skill mode may write within the case workspace and run commands the
        # bound Skill documents; baseline remains static read-only inspection.
        'tool-fs': dict(read=True, readImage=False, write=bool(skill), edit=bool(skill)),
        'agent-default-model': dict(provider='benchmark', model=model),
        'approval': dict(policy='never'),
        'session-persistence-jsonl': dict(root=paths['home'] + '/sessions', compression='none'),
        'sandbox-policy': dict(mode=sandbox_mode, workspaceRoot=paths['repo']),
        'compaction-basic': dict(compactionRetries=0, maxOverflowRetries=0),
        'llm-pi-ai': dict(providers={'benchmark': dict(
            api='openai-completions', baseURL='http://127.0.0.1:8123/v1',
            apiKeyEnv='BENCH_DSH_KEY', models=[dict(id=model, contextWindow=131072,
                                                 maxTokens=runtime.output_limit(max_output_tokens))],
            streamIdleTimeoutMs=STREAM_IDLE_TIMEOUT_MS,
            retryPolicy=dict(mode='normal', maxRetries=0))}),
    }
    patches += [dict(id=name, config=config) for name, config in settings.items()]
    patches += [dict(id='compaction-basic', disabled=True),
                dict(insert=[dict(id='benchmark-compaction', name=runtime.script('compaction.mjs'),
                                  config=dict(compactionRetries=1, maxOverflowRetries=1,
                                              headroomTokens=16384))]),
                dict(insert=[dict(id='benchmark-tool-scope', name=runtime.script('scope.mjs'),
                                  config=dict(paths=paths, commands=bool(skill), writes=bool(skill)))]),
                dict(id='headless-runner', inject=['headlessStartup', 'compaction', 'benchmarkScope'])]
    if skill:
        patches += [dict(insert=[dict(id='benchmark-skill', name=runtime.script('candidate.mjs'),
                                    config=dict(skill, root=paths['skill']))]),
                    dict(id='headless-runner', inject=['headlessStartup', 'compaction', 'benchmarkScope', 'benchmarkSkill'])]
    return json.dumps(patches)
