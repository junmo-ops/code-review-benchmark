"""Load only the selected reviewer; optional runtimes must not block the UI."""
from importlib import import_module
import platform

REVIEWERS = {
    "lcr": "reviewers.lcr.adapter",
    "server_agent": "reviewers.server_agent.adapter",
    "cline_skill": "reviewers.cline_skill.adapter",
    "dsh_skill": "reviewers.dsh_skill.adapter",
}


def get_reviewer(name):
    try:
        module = REVIEWERS[name]
    except KeyError:
        raise ValueError(f"未接入的检视工具：{name}") from None
    if name == 'dsh_skill' and platform.system() != 'Linux':
        raise ValueError('DSH 检视需要 Linux 运行环境；本机可使用 LCR 或服务端 Agent')
    return import_module(module)
