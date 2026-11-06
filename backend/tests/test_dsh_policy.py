"""Policy overlay smoke tests: the generated DSH patch must carry the tuned
stream-idle timeout on the benchmark provider, and structure must stay stable."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reviewers.dsh_skill import policy, runtime

MINIMAL_BUNDLE = '''\
- insert:
    - id: tool-fs
      name: "@benchmark/tool-fs"
    - id: llm-pi-ai
      name: "@deepseek-ai/dsh-llm-pi-ai"
    - id: headless-runner
      name: "@benchmark/headless-runner"
'''


class PolicyOverlayTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        modules = Path(self.directory.name)
        # policy.overlay reads MODULES / '@deepseek-ai/dsh-base/cordis.patch.yml'
        base = modules / '@deepseek-ai' / 'dsh-base'
        base.parent.mkdir(parents=True)
        base.mkdir()
        (base / 'cordis.patch.yml').write_text(MINIMAL_BUNDLE)

    def overlay(self):
        with mock.patch.object(runtime, 'MODULES', Path(self.directory.name)):
            return json.loads(policy.overlay('deepseek-model', max_output_tokens=8192))

    def test_overlay_stream_idle_timeout_is_set_on_benchmark_provider(self):
        patches = self.overlay()
        llm = next(p for p in patches if p.get('id') == 'llm-pi-ai')
        provider = llm['config']['providers']['benchmark']
        self.assertEqual(provider['streamIdleTimeoutMs'], policy.STREAM_IDLE_TIMEOUT_MS)
        self.assertEqual(provider['maxRetries'] if 'maxRetries' in provider else provider['retryPolicy']['maxRetries'], 0)

    def test_overlay_keeps_frozen_model_surface(self):
        patches = self.overlay()
        llm = next(p for p in patches if p.get('id') == 'llm-pi-ai')
        model = llm['config']['providers']['benchmark']['models'][0]
        self.assertEqual(model['id'], 'deepseek-model')
        self.assertEqual(model['maxTokens'], 8192)
        self.assertEqual(model['contextWindow'], 131072)
        disabled = [p['id'] for p in patches if p.get('disabled')]
        self.assertNotIn('llm-pi-ai', disabled)


if __name__ == '__main__':
    unittest.main()
