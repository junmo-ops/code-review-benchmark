"""Test-only worker/CLI wrapper. Real queue and sandbox, deliberately NOT native DSH."""
from pathlib import Path
import os
import sys

from reviewers.dsh_skill import runtime
from webui.queue import DurableDashboard

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/e2e/dsh-controlled.mjs'
ORIGINAL_COMMAND = runtime.command


def changed_suite_identity(suite):
    identity = ORIGINAL_IDENTITY(suite)
    identity['files']['backend/src/suites/aacr/evaluator.py'] = 'f' * 64
    return identity


def controlled_command(work, prompt):
    if runtime.runtime_mode() == 'pod':
        return [str(runtime.node()), str(SCRIPT)]
    command = ORIGINAL_COMMAND(work, prompt)
    index = command.index('--import') - 1
    return command[:index] + ['--ro-bind', str(SCRIPT), '/controlled.mjs', '/node', '/controlled.mjs']


class ControlledDashboard(DurableDashboard):
    def command(self, *args, **kwargs):
        command, env, limit = super().command(*args, **kwargs)
        entry = str(self.root / 'backend/src/cloud.py')
        if entry in command:
            command[command.index(entry)] = str(Path(__file__).resolve())
        return command, env, limit


if __name__ == '__main__':
    if '--worker' in sys.argv:
        sys.argv.remove('--worker')
        from webui import worker
        worker.DurableDashboard = ControlledDashboard
        worker.main()
    else:
        runtime.command = controlled_command
        if os.getenv('DSH_CONTROLLED_SUITE_CHANGED') == '1' and sys.argv[1] in {'review', 'eval'}:
            from suites.aacr.adapter import AACRSuite
            ORIGINAL_IDENTITY = AACRSuite.identity
            AACRSuite.identity = changed_suite_identity
        if os.getenv('DSH_CONTROLLED_CLEANUP_FAIL') == '1':
            from reviewers.dsh_skill import supervisor
            original_terminate = supervisor._terminate

            def deny_cleanup(process):
                original_terminate(process)
                raise PermissionError('injected cleanup confirmation failure')

            supervisor._terminate = deny_cleanup
        from runner.cloud import main
        try:
            main()
        except KeyboardInterrupt:
            raise SystemExit(130) from None
