"""Source drift must invalidate frozen comparison protocols after directory migration."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from comparison.scorer import identity


class ComparisonIdentityTests(unittest.TestCase):
    def test_comparison_implementation_is_part_of_the_frozen_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ['backend/src/comparison/scorer.py', 'backend/src/comparison/runner.py',
                     'backend/src/evaluators/martian.py',
                     'vendor/martian/offline/code_review_benchmark/step3_judge_comments.py']
            for name in paths:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('# original', encoding='utf-8')
            with patch('comparison.scorer.ROOT', root):
                frozen = identity()
                (root / paths[0]).write_text('# changed scoring behavior', encoding='utf-8')
                changed = identity()
            self.assertEqual(set(frozen), set(paths))
            self.assertNotEqual(frozen, changed)
            self.assertNotEqual(frozen[paths[0]], changed[paths[0]])
