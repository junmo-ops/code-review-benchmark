"""Explicit reviewer registry. Add adapters here after capability verification."""
from reviewers.lcr import adapter as lcr
from reviewers.cline_skill import adapter as cline_skill
from reviewers.server_agent import adapter as server_agent
REVIEWERS = {"lcr": lcr, "server_agent": server_agent, "cline_skill": cline_skill}

def get_reviewer(name):
    try:
        return REVIEWERS[name]
    except KeyError:
        raise ValueError(f"未接入的检视工具：{name}") from None
