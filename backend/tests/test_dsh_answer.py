"""Final answer format failures are explicit, never successful empty reviews."""
import json
import os
import unittest
from unittest.mock import patch

from dsh_probe import case
from reviewers.dsh_skill import answer
from runner.contracts import ReviewCase
from tools.dsh_local_model import serve


class AnswerFormatTests(unittest.TestCase):
    def convert(self, text, reason='completed'):
        item = ReviewCase('local.case', 'test/repo', 'base', 'head')
        rows = [dict(type='session', sessionId='local-session'),
                dict(type='status', phase='turn_end', reason=dict(kind=reason)),
                dict(type='final', text=text)]
        cfg = dict(review_model='local-model', tool_hashes={'local': True})
        return answer.convert(item, cfg, rows, {'sample.py': 1}, [])

    def test_three_newline_final_is_reported_as_blank_not_json_decoder_failure(self):
        with self.assertRaisesRegex(ValueError, '最终答卷为空白'):
            self.convert('\n\n\n')

    def test_empty_and_non_text_finals_do_not_become_empty_reviews(self):
        for text in ('', ' \t\r\n', None, [], {}):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, '最终答卷'):
                self.convert(text)

    def test_non_json_error_has_position_but_does_not_echo_model_content(self):
        with self.assertRaises(ValueError) as failure:
            self.convert('\n\n\nPRIVATE_MODEL_TEXT')
        self.assertNotIsInstance(failure.exception, json.JSONDecodeError)
        self.assertIn('第 4 行', str(failure.exception))
        self.assertIn('第 1 列', str(failure.exception))
        self.assertNotIn('PRIVATE_MODEL_TEXT', str(failure.exception))

    def test_markdown_or_partial_json_is_not_silently_repaired(self):
        for text in ('```json\n{"status":"complete","comments":[]}\n```',
                     '{"status":"complete","comments":[',
                     '{"status":"complete","comments":[]} extra'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, '有效 JSON'):
                self.convert(text)

    def test_explicit_valid_empty_review_still_succeeds(self):
        result = self.convert('\n{"status":"complete","comments":[]}\n')
        self.assertEqual(result['review']['status'], 'complete')
        self.assertEqual(result['review']['comments'], [])

    def test_end_marker_schema_and_head_line_validation_remain_required(self):
        with self.assertRaisesRegex(ValueError, '未正常完成'):
            self.convert('{"status":"complete","comments":[]}', 'max-tokens')
        with self.assertRaisesRegex(ValueError, '完整 JSON 协议'):
            self.convert('{}')
        with self.assertRaisesRegex(ValueError, '行号越界'):
            self.convert(json.dumps(dict(status='complete', comments=[dict(
                path='sample.py', start_line=2, end_line=2, content='out of bounds')])))


class NativeBlankAnswerTests(unittest.TestCase):
    def test_actual_dsh_completed_whitespace_remains_failed_without_a_retry_or_recovery_call(self):
        with patch.dict(os.environ, BENCH_DSH_RUNTIME_MODE='pod'), serve(
                lambda body, index: (dict(content='\n\n\n',
                    reasoning_content='LOCAL_REASONING_NOT_A_FINAL_ANSWER'), 'stop')) as model:
            with case(model, max_output_tokens=31248) as probe:
                result = probe.review()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('最终答卷为空白', result.stdout + result.stderr)
                self.assertNotIn('JSONDecodeError', result.stdout + result.stderr)
                self.assertEqual(len(model.requests), 1)
                self.assertFalse((probe.run / 'results/local.case.json').exists())
                rows = [json.loads(line) for line in
                        (probe.run / 'raw/local.case.stdout.jsonl').read_text().splitlines()]
                self.assertEqual([row['reason'] for row in rows if row.get('phase') == 'turn_end'],
                                 [{'kind': 'completed'}])
                self.assertEqual([row['text'] for row in rows if row.get('type') == 'final'], ['\n\n\n'])
                requests = json.loads((probe.run / 'raw/local.case.requests.json').read_text())
                self.assertEqual(requests[0]['wire_response']['finish_reason'], 'stop')
                self.assertEqual(requests[0]['wire_response']['delta_chars'], 3)
                self.assertFalse(requests[0]['wire_response']['delta_nonblank'])
                self.assertGreater(requests[0]['wire_response']['reasoning_chars'], 0)
                self.assertTrue(requests[0]['wire_response']['done_marker'])
                recovered = probe.stage('recover', '--instance', 'local.case')
                self.assertNotEqual(recovered.returncode, 0)
                self.assertIn('最终答卷为空白', recovered.stdout + recovered.stderr)
                self.assertEqual(len(model.requests), 1)
                self.assertFalse((probe.run / 'results/local.case.json').exists())


if __name__ == '__main__':
    unittest.main()
