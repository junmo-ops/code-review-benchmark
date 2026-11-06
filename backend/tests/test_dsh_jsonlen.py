"""Tolerant JSON extraction and the skill-support scan over the final answer."""
import json
import unittest

import pytest

from reviewers.dsh_skill import answer, loading
from reviewers.dsh_skill import jsonextract


class ExtractTests(unittest.TestCase):
    def extract(self, text):
        return jsonextract.extract_json_object(text)

    def test_none_and_malformed_inputs(self):
        self.assertIsNone(self.extract(None))
        self.assertIsNone(self.extract(''))
        self.assertIsNone(self.extract('no braces here'))
        self.assertIsNone(self.extract('{"status":"complete","comments":['))

    def test_plain_object(self):
        self.assertEqual(self.extract('{"a":1}'), '{"a":1}')

    def test_braces_inside_strings_do_not_break_the_scan(self):
        text = '他说 "{" 然后 } 结束，再 {"a":"}"} 收尾'
        self.assertEqual(self.extract(text), '{"a":"}"}')

    def test_escaped_quotes_inside_strings(self):
        text = '{"a":"say \\" ok","b":{"c":1}} trailing'
        self.assertEqual(self.extract(text), '{"a":"say \\" ok","b":{"c":1}}')

    def test_nested_objects_balance(self):
        text = '{"outer":{"inner":{"deep":1}},"tail":2}'
        self.assertEqual(self.extract(text), text)

    def test_array_whole_text_parse_takes_precedence(self):
        self.assertEqual(self.extract('["a",{"b":1}]'), '{"b":1}')
        self.assertEqual(jsonextract.loads_tolerant('["a",{"b":1}]'), ['a', {'b': 1}])

    def test_last_complete_object_wins(self):
        text = '例：{"status":"complete","comments":[]} 真实答案 {"status":"complete","comments":[{"path":"a.py","start_line":1,"end_line":1,"content":"x"}]}'
        self.assertEqual(jsonextract.loads_tolerant(text)['comments'][0]['path'], 'a.py')

    def test_prose_wrapped_object_is_isolated(self):
        text = ('我核对了 head 快照与差异，发现以下缺陷：\n \n'
                '```json\n{"status":"complete","comments":[{"path":"sample.py",'
                '"start_line":1,"end_line":1,"content":"缺陷"}]}\n```\n'
                '（以上意见仅供评审参考）')
        parsed = jsonextract.loads_tolerant(text)
        self.assertEqual(parsed['status'], 'complete')
        self.assertEqual(len(parsed['comments']), 1)

    def test_loads_tolerant_whole_text_first(self):
        self.assertEqual(jsonextract.loads_tolerant('{"status":"complete","comments":[]}'),
                         {'status': 'complete', 'comments': []})
        self.assertIsNone(jsonextract.loads_tolerant('{"a":'))
        self.assertIsNone(jsonextract.loads_tolerant(None))


class ParseFinalTests(unittest.TestCase):
    def parse(self, text):
        return answer._parse_final(text)

    def test_strict_pure_json_still_used_first(self):
        self.assertEqual(self.parse('{"status":"complete","comments":[]}'), {'status': 'complete', 'comments': []})

    def test_archived_dbeaver_shape_is_salvaged(self):
        # Mirrors the archived dbeaver/gemini final text: prose paragraphs then
        # the unsupported verdict object; the old whole-text parse failed at
        # line 3 column 1.
        text = ('我完成了对 head 快照与差异的核验。\n'
                '检查了源码目录并比对 /input/REVIEW_DIFF.patch。\n'
                '{"status":"unsupported","reason":"该 Skill 需要运行 prepare-review.py 脚本并写文件，本环境只读"}\n'
                '因此不发布质量分。')
        self.assertEqual(self.parse(text)['status'], 'unsupported')

    def test_prose_before_and_after_complete_object(self):
        text = ('分析完毕。\n'
                '{"status":"complete","comments":[{"path":"sample.py","start_line":1,'
                '"end_line":1,"content":"问题"}]}\n汇报结束。')
        self.assertEqual(self.parse(text)['comments'][0]['path'], 'sample.py')

    def test_non_protocol_javascript_values_parse_but_fail_protocol_validation(self):
        # _parse_final only guarantees a JSON value; protocol shape (a dict with
        # status complete + comments) is enforced by answer.convert.
        self.assertEqual(self.parse('[]'), [])
        self.assertEqual(self.parse('"just a string"'), 'just a string')
        with self.assertRaises(ValueError):
            self.parse('{"status":"complete","comments":[]')  # not balanced


class SupportScanTests:
    """Plain class: pytest-style tempdir fixtures, no unittest.TestCase."""

    def check(self, tmp_path, rows, events, allowed):
        return loading._require_supported(tmp_path / 'case', rows, events, set(allowed))

    def test_prose_wrapped_unsupported_verdict_is_found(self, tmp_path):
        rows = []
        events = [dict(type='final', text='我无法执行该 Skill 的脚本。\n'
                    '{"status":"unsupported","reason":"需要 bash 与写文件能力"}\n见上。')]
        with pytest.raises(ValueError, match='Skill 依赖禁用能力'):
            self.check(tmp_path, rows, events, ['read', 'glob', 'grep', 'skill'])
        record = json.loads((tmp_path / 'case.skill-support.json').read_text())
        assert record['status'] == 'unsupported'
        assert any(item['name'] == 'declared_requirement' for item in record['basis'])

    def test_legit_skill_workflow_is_supported_when_tools_allowed(self, tmp_path):
        rows = [
            dict(type='tool/call', seq=1, data=dict(name='bash', arguments={'command': 'python prepare-review.py'})),
            dict(type='tool/call', seq=2, data=dict(name='write', arguments={'file_path': 'issues.json'})),
            dict(type='tool/call', seq=3, data=dict(name='read', arguments={'file_path': 'sample.py'})),
        ]
        record = self.check(tmp_path, rows, [], ['read', 'glob', 'grep', 'write', 'edit', 'bash', 'skill'])
        assert record['status'] == 'supported'
        assert record['basis'] == []

    def test_disabled_tools_still_count_as_dependency(self, tmp_path):
        rows = [dict(type='tool/call', seq=1, data=dict(name='write', arguments={'file_path': 'issues.json'}))]
        with pytest.raises(ValueError):
            self.check(tmp_path, rows, [], ['read', 'glob', 'grep', 'skill'])
        record = json.loads((tmp_path / 'case.skill-support.json').read_text())
        assert record['basis'][0]['name'] == 'write'

    def test_baseline_scan_rejects_any_extra_tool(self, tmp_path):
        rows = [dict(type='tool/call', seq=1, data=dict(name='web_fetch', arguments={}))]
        with pytest.raises(ValueError):
            self.check(tmp_path, rows, [], ['read', 'glob', 'grep'])

    def test_path_scope_denials_do_not_veto_a_complete_review(self, tmp_path):
        # A probing glob of the case root or the skill resource directory is
        # denied by the software guard; the Skill still delivered a complete
        # review through allowed paths, so the denial is audit-only.
        rows = [
            dict(type='tool/call', seq=1, data=dict(name='glob', callId='c1',
                 arguments={'pattern': '*', 'path': '/tmp/bench-dsh-x'})),
            dict(type='tool/result', seq=2, data=dict(message=dict(
                toolCallId='c1', isError=True, content=[dict(type='text', text=
                'Error: BENCH_DSH_SCOPE_DENIED: inspection access outside the case and task input is denied')]))),
        ]
        events = [dict(type='final', text='{"status":"complete","comments":[]}')]
        record = self.check(tmp_path, rows, events, ['read', 'glob', 'grep', 'skill'])
        assert record['status'] == 'supported'
        assert record['basis'] == []
        assert len(record['denied_exploration']) == 1

    def test_path_scope_denials_without_an_answer_stay_dependency_evidence(self, tmp_path):
        # No complete review was delivered: the denials remain evidence that
        # the Skill could not satisfy its workflow inside the allowed surface.
        rows = [
            dict(type='tool/call', seq=1, data=dict(name='glob', callId='c1',
                 arguments={'pattern': '*', 'path': '/etc'})),
            dict(type='tool/result', seq=2, data=dict(message=dict(
                toolCallId='c1', isError=True, content=[dict(type='text', text=
                'Error: BENCH_DSH_SCOPE_DENIED: inspection access outside the case and task input is denied')]))),
        ]
        events = [dict(type='final', text='我未能完成检视。')]
        with pytest.raises(ValueError, match='Skill 依赖禁用能力'):
            self.check(tmp_path, rows, events, ['read', 'glob', 'grep', 'skill'])


if __name__ == '__main__':
    unittest.main()
