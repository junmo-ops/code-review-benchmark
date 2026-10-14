"""External LCR binary identity is simulated only in the isolated Linux harness."""
import os


def tool_identity():
    return {'e2e-lcr': 'deterministic-simulation-v1'}


def configure():
    if os.environ.get('E2E_SIMULATED_LCR') == '1':
        from reviewers.lcr import adapter
        adapter.tool_identity = tool_identity
