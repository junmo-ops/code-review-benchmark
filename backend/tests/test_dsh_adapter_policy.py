"""Skill-mode capability enablement, compaction config, and failure wording."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reviewers.dsh_skill import adapter, policy, runtime


def _bundle_root():
    """Frozen bundle source: repo install, else the local offline DSH checkout.

    ``dependencies/dsh`` keeps only the frozen records until ``npm ci`` runs on
    the Linux pod; the offline DSH install ships the identical bundle.
    """
    candidates = [Path('dependencies/dsh/node_modules'),
                  Path(os.environ.get('DSH_OFFLINE_ROOT', 'D:\\devSoftware\\dsh\\node_modules'))]
    for candidate in candidates:
        if (candidate / '@deepseek-ai/dsh-base/cordis.patch.yml').is_file():
            return candidate
    return None


def _overlay_rows(skill, runtime_mode='pod'):
    root = _bundle_root()
    if root is None:
        raise unittest.SkipTest('缺少冻结 DSH bundle（cordis.patch.yml），跳过 overlay 测试')
    import reviewers.dsh_skill.runtime as runtime_module
    with patch.dict(os.environ, BENCH_DSH_RUNTIME_MODE=runtime_mode), \
            patch.object(runtime_module, 'MODULES', root), \
            tempfile.TemporaryDirectory(prefix='dsh-policy-') as directory:
        return json.loads(policy.overlay('bench-model', skill, Path(directory)))


def _index(rows):
    """Flatten overlay patches: both direct id rows and insert-wrapped rows."""
    by_id, disabled = {}, set()
    for row in rows:
        if 'insert' in row:
            for inserted in row['insert']:
                by_id[inserted['id']] = inserted
                if inserted.get('disabled'):
                    disabled.add(inserted['id'])
        else:
            by_id[row['id']] = row
            if row.get('disabled'):
                disabled.add(row['id'])
    return by_id, disabled


class PolicyOverlayTests(unittest.TestCase):
    def test_baseline_stays_read_only_inspection(self):
        rows = _overlay_rows(None)
        by_id, disabled = _index(rows)
        self.assertIn('bash-sandbox', disabled)
        self.assertIn('tool-bash', disabled)
        self.assertEqual(by_id['tool-fs']['config']['write'], False)
        self.assertEqual(by_id['tool-fs']['config']['edit'], False)
        self.assertEqual(by_id['sandbox-policy']['config']['mode'], 'read-only')
        scope = by_id['benchmark-tool-scope']['config']
        self.assertEqual(scope['commands'], False)
        self.assertEqual(scope['writes'], False)
        compaction = by_id['benchmark-compaction']['config']
        self.assertEqual(compaction['compactionRetries'], 1)
        self.assertEqual(compaction['maxOverflowRetries'], 1)
        self.assertEqual(compaction['headroomTokens'], 16384)

    def test_skill_mode_enables_write_edit_bash(self):
        rows = _overlay_rows(dict(name='candidate-skill'))
        by_id, disabled = _index(rows)
        self.assertNotIn('bash-sandbox', disabled)
        self.assertNotIn('tool-bash', disabled)
        self.assertEqual(by_id['tool-fs']['config']['write'], True)
        self.assertEqual(by_id['tool-fs']['config']['edit'], True)
        self.assertEqual(by_id['sandbox-policy']['config']['mode'], 'danger-full-access')
        scope = by_id['benchmark-tool-scope']['config']
        self.assertEqual(scope['commands'], True)
        self.assertEqual(scope['writes'], True)
        self.assertTrue(str(scope['paths']['repo']).endswith(('repo', 'repo\\', 'repo/')))
        self.assertEqual(by_id['benchmark-skill']['config']['name'], 'candidate-skill')

    def test_sandbox_mode_skill_keeps_workspace_write_confinement(self):
        rows = _overlay_rows(dict(name='candidate-skill'), runtime_mode='sandbox')
        by_id, _ = _index(rows)
        self.assertEqual(by_id['sandbox-policy']['config']['mode'], 'workspace-write')


class PolicyFrozenMetaTests(unittest.TestCase):
    def test_runtime_policy_reflects_enabled_surface(self):
        with patch.dict(os.environ, BENCH_DSH_RUNTIME_MODE='pod'):
            self.assertEqual(runtime.policy('skill')['tools'],
                             ['read', 'glob', 'grep', 'write', 'edit', 'bash', 'skill'])
            self.assertEqual(runtime.policy('baseline')['tools'], ['read', 'glob', 'grep'])
            self.assertEqual(runtime.policy('skill')['compaction_retries'], 1)
            self.assertEqual(runtime.policy('skill')['overflow_retries'], 1)


class ExecutionClassificationTests(unittest.TestCase):
    def rows(self, reason):
        return json.dumps(dict(type='status', phase='turn_end', reason=reason)) + '\n'

    def test_compaction_cancel_is_classified(self):
        message = adapter._execution_error(self.rows(dict(kind='aborted', reason=dict(kind='user'))))
        self.assertIn('DSH 回合被中止', message)
        self.assertIn('compaction', message)

    def test_max_tokens_stays_specific(self):
        message = adapter._execution_error(self.rows(dict(kind='max-tokens')))
        self.assertIn('输出截断', message)

    def test_completed_abnormal_exit_is_generic(self):
        message = adapter._execution_error(self.rows(dict(kind='completed')))
        self.assertEqual(message, 'DSH 执行失败，详见脱敏原始日志')

    def test_timeout_error_is_classified(self):
        message = adapter._execution_error(self.rows(dict(kind='error', error=dict(
            message='Request timed out. Node.js fetch timed out waiting for response headers',
            code='TIMEOUT'))))
        self.assertIn('TIMEOUT', message)
        self.assertIn('模型通道请求超时', message)

    def test_malformed_log_is_generic(self):
        self.assertEqual(adapter._execution_error('not json at all'), 'DSH 执行失败，详见脱敏原始日志')


class RetryClassificationTests(unittest.TestCase):
    def test_only_answer_format_failures_are_retried(self):
        self.assertTrue(adapter._retryable_failure(ValueError('DSH 最终答卷不是有效 JSON')))
        self.assertFalse(adapter._retryable_failure(ValueError('DSH 执行失败，详见脱敏原始日志')))
        self.assertFalse(adapter._retryable_failure(ValueError('Skill 依赖禁用能力')))
        self.assertFalse(adapter._retryable_failure(runtime.IsolationError('隔离启动失败')))


if __name__ == '__main__':
    unittest.main()
