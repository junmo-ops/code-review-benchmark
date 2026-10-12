import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('configure_qwen', ROOT/'tools/configure_qwen.py')
qwen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qwen)


class QwenProfileTests(unittest.TestCase):
    def test_both_environments_preserve_judge_and_are_discovered(self):
        from webui.service import Dashboard
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = {'LCR_LLM_TOKEN': 'old-key', 'JUDGE_API_KEY': 'judge-key',
                        'JUDGE_BASE_URL': 'https://judge.example/v1', 'JUDGE_MODEL': 'judge'}
            source = root/'model.local.json'
            source.write_text(json.dumps(original))
            before = source.read_bytes()
            with patch.dict(os.environ, {'BENCH_CONFIG_ROOT': tmp}):
                for env, base in [('aliyun', 'https://cloud.example/compatible-mode/v1'),
                                  ('internal', 'http://internal.example/v1')]:
                    argv = ['configure_qwen.py', '--environment', env, '--model', 'qwen-test', '--base-url', base]
                    with patch.object(sys, 'argv', argv), patch.object(qwen.getpass, 'getpass', return_value='new-key'):
                        qwen.main()
                    target = root/f'model.qwen.{env}.local.json'
                    config = json.loads(target.read_text())
                    self.assertEqual(config['LCR_LLM_BASE_URL'], base)
                    self.assertEqual(config['JUDGE_API_KEY'], 'judge-key')
                    self.assertEqual(config['LCR_LLM_TOKEN'], 'new-key')
                    self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                    with patch.object(sys, 'argv', argv), patch.object(qwen.getpass, 'getpass') as prompt:
                        with self.assertRaises(SystemExit):
                            qwen.main()
                        prompt.assert_not_called()
                self.assertEqual(source.read_bytes(), before)
                dashboard = object.__new__(Dashboard)
                dashboard.configs = root
                profiles = dashboard.profiles()
                self.assertEqual(len([p for p in profiles if p['review_model'] == 'qwen-test']), 2)
                self.assertNotIn('new-key', json.dumps(profiles))
                self.assertNotIn('judge-key', json.dumps(profiles))

    def test_invalid_url_does_not_prompt_for_secret_or_write(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'BENCH_CONFIG_ROOT': tmp}):
            for url in ['https://user:secret@host/v1', 'https://host/v1?key=secret',
                        'https://host/v1/chat/completions', 'https://{WorkspaceId}.example/v1']:
                with patch.object(sys, 'argv', ['configure_qwen.py', '--model', 'qwen', '--base-url', url]), patch.object(qwen.getpass, 'getpass') as prompt:
                    with self.assertRaises(SystemExit):
                        qwen.main()
                    prompt.assert_not_called()
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
