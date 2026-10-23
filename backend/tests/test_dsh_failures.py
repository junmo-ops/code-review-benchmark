"""Failure injection into real Linux DSH through loopback model responses."""
import json
import os
import shutil
import time
import unittest
from dsh_probe import case
from tools.dsh_local_model import serve


class DSHFailureTests(unittest.TestCase):
    def test_failed_cleanup_cannot_recover_otherwise_complete_output(self):
        reply = lambda body, index: (dict(content='{"status":"complete","comments":[]}'), 'stop')
        with serve(reply) as model, case(model) as probe:
            result = probe.review()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            answer = probe.run / 'results/local.case.json'
            saved = answer.read_bytes()
            answer.unlink()
            record_path = probe.run / 'raw/local.case.execution.json'
            record = json.loads(record_path.read_text())
            self.assertEqual(record['status'], 'exited')
            self.assertEqual(record['exit_code'], 0)
            record['cleanup_status'] = 'failed'
            record_path.write_text(json.dumps(record))
            recovered = probe.stage('recover', '--instance', 'local.case')
            self.assertNotEqual(recovered.returncode, 0)
            self.assertFalse(answer.exists())
            self.assertEqual(len(model.requests), 1)
            # Legacy records were finalized only after cleanup succeeded.
            record.pop('cleanup_status')
            record_path.write_text(json.dumps(record))
            recovered = probe.stage('recover', '--instance', 'local.case')
            self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
            self.assertEqual(answer.read_bytes(), saved)
            self.assertEqual(len(model.requests), 1)

    def test_symlink_parent_traversal_cannot_read_runtime_context(self):
        def prepare(repo):
            (repo / 'link').symlink_to('.')
            decoy = repo / 'runtime/node_modules/@deepseek-ai/dsh/README.md'
            decoy.parent.mkdir(parents=True)
            decoy.write_text('REPOSITORY_DECOY_ONLY\n')

        def attack(body, index):
            if index != 1:
                return dict(content='{"status":"complete","comments":[]}'), 'stop'
            directory = 'link/../runtime/node_modules/@deepseek-ai/dsh'
            calls = [('read', dict(file_path=directory + '/README.md')),
                     ('grep', dict(pattern='dsh', path=directory)),
                     ('glob', dict(pattern='README.md', path=directory))]
            return dict(content=None, tool_calls=[dict(id=str(i), type='function',
                function=dict(name=name, arguments=json.dumps(args)))
                for i, (name, args) in enumerate(calls)]), 'tool_calls'

        with serve(attack) as model, case(model, prepare_head=prepare) as probe:
            result = probe.review()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(len(model.requests), 2)
            outcomes = [m for m in model.requests[-1]['messages'] if m['role'] == 'tool']
            self.assertEqual(len(outcomes), 3)
            self.assertTrue(all('inspection access outside' in json.dumps(m).lower()
                                for m in outcomes), outcomes)

    def test_missing_isolation_stops_the_batch_without_a_model_request(self):
        with serve() as model, case(model) as probe:
            binaries = probe.root / 'bin'
            binaries.mkdir()
            (binaries / 'node').symlink_to(shutil.which('node'))
            probe.env['PATH'] = str(binaries)
            result = probe.review()
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((probe.run / 'dsh-system-error.json').is_file())
            self.assertEqual(model.requests, [])
            self.assertFalse((probe.run / 'results/local.case.json').exists())
    def test_carriage_return_without_newline_does_not_create_a_legal_head_line(self):
        text = json.dumps(dict(status='complete', comments=[dict(path='sample.py',
            start_line=2, end_line=2, content='invalid physical line')]))
        with serve(lambda body, index: (dict(content=text), 'stop')) as model, case(model, head_text='value = 2\rnot another line\n') as probe:
            result = probe.review()
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((probe.run / 'results/local.case.json').exists())

    def test_review_endpoint_cannot_change_after_freezing(self):
        with serve() as original, serve() as changed, case(original) as probe:
            probe.env['LCR_LLM_BASE_URL'] = changed.url
            result = probe.review()
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(len(original.requests), 0)
            self.assertEqual(len(changed.requests), 0)

    def test_successful_fixed_compaction_is_metered_with_normal_tool_turns(self):
        def response(body, index):
            if index <= 2:
                model.usage = (dict(prompt_tokens=100, completion_tokens=38000, total_tokens=38100)
                               if index == 1 else dict(prompt_tokens=39000, completion_tokens=38000, total_tokens=77000))
                return dict(content='historical context ' * 8000, tool_calls=[dict(
                    id=f'read-{index}', type='function', function=dict(name='read',
                    arguments=json.dumps(dict(file_path='/work/sample.py'))))]), 'tool_calls'
            if 'compaction engine' in json.dumps(body).lower():
                model.usage = dict(prompt_tokens=39000, completion_tokens=40, total_tokens=39040)
                return dict(content='## Current Work\nReview sample.py; the task requires complete JSON.\n'), 'stop'
            model.usage = dict(prompt_tokens=40000, completion_tokens=10, total_tokens=40010)
            return dict(content='{"status":"complete","comments":[]}'), 'stop'
        with serve(response) as model, case(model) as probe:
            result = probe.review()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(len(model.requests), 4)
            self.assertTrue('compaction engine' in json.dumps(model.requests[2]).lower())
            answer = json.loads((probe.run / 'results/local.case.json').read_text())
            self.assertEqual(answer['review']['summary']['total_tokens'], 194150)
            self.assertEqual(answer['review']['usage_coverage'], {'known_requests': 4, 'requests': 4})

    def test_search_tools_work_without_a_project_shell(self):
        def response(body, index):
            if index == 1:
                return dict(content=None, tool_calls=[dict(id='search', type='function',
                    function=dict(name='grep', arguments=json.dumps(dict(pattern='value', path='/work'))))]), 'tool_calls'
            return dict(content='{"status":"complete","comments":[]}'), 'stop'
        with serve(response) as model, case(model) as probe:
            result = probe.review()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            tools = [m for m in model.requests[-1]['messages'] if m['role'] == 'tool']
            self.assertEqual(len(tools), 1)
            self.assertIn('value = 2', json.dumps(tools))

    def test_null_cache_details_do_not_invalidate_known_usage(self):
        reply = dict(content='{"status":"complete","comments":[]}')
        with serve(lambda body, index: (reply, 'stop')) as model, case(model) as probe:
            model.usage = dict(prompt_tokens=100, completion_tokens=10, total_tokens=110,
                               prompt_tokens_details=None)
            result = probe.review()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            answer = json.loads((probe.run / 'results/local.case.json').read_text())
            self.assertEqual(answer['review']['summary']['total_tokens'], 110)
            self.assertNotIn('cache_read_tokens', answer['review']['summary'])

    def test_compaction_failure_is_not_retried(self):
        def response(body, index):
            if index == 1:
                return dict(content='historical context ' * 8000, tool_calls=[dict(
                    id=f'read-{index}', type='function', function=dict(name='read',
                    arguments=json.dumps(dict(file_path='/work/sample.py'))))]), 'tool_calls'
            return dict(http_status=503, http_error={'error': {'code': 'server_error',
                'message': 'injected compaction failure'}}), 'stop'
        with serve(response) as model, case(model) as probe:
            model.usage = dict(prompt_tokens=100000, completion_tokens=10, total_tokens=100010)
            result = probe.review()
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(len(model.requests), 2)
            self.assertTrue('compaction engine' in json.dumps(model.requests[-1]).lower(),
                            'normal history growth did not trigger the fixed compaction policy')
            self.assertFalse((probe.run / 'results/local.case.json').exists())

    def test_only_read_tools_are_exposed_and_hidden_context_stays_unreadable(self):
        target = {}
        def attack(body, index):
            if index == 1:
                calls = [('write', {'file_path': '/work/sample.py', 'content': 'corrupted'}),
                         ('bash', {'command': 'npm install && ./skill.sh'}),
                         ('subagent', {'prompt': 'delegate'}),
                         ('web_fetch', {'url': 'http://127.0.0.1:8123/evil'}),
                         ('read', {'file_path': str(target['path'])}),
                         ('read', {'file_path': '/runtime/node_modules/@deepseek-ai/dsh/README.md'}),
                         ('grep', {'pattern': 'dsh', 'path': '/runtime'}),
                         ('glob', {'pattern': '**/*', 'path': '/home'})]
                return dict(content=None, tool_calls=[dict(id=str(i), type='function',
                    function=dict(name=name, arguments=json.dumps(args)))
                    for i, (name, args) in enumerate(calls)]), 'tool_calls'
            return dict(content='{"status":"complete","comments":[]}'), 'stop'
        with serve(attack) as model, case(model) as probe:
            target['path'] = probe.root / 'dataset.jsonl'
            result = probe.review()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(len(model.requests), 2)
            first = model.requests[0]
            self.assertEqual({t['function']['name'] for t in first['tools']}, {'read', 'glob', 'grep'})
            for sentinel in ('REPOSITORY_INSTRUCTION_MUST_NOT_LOAD', 'REPOSITORY_ENV_MUST_NOT_LOAD',
                             'REFERENCE_MUST_NOT_BE_VISIBLE', 'local-secret', 'judge-secret'):
                self.assertNotIn(sentinel, json.dumps(model.requests))
            outcomes = [m for m in model.requests[1]['messages'] if m['role'] == 'tool']
            self.assertEqual(len(outcomes), 8)
            self.assertTrue(all('error' in json.dumps(m).lower() or 'not found' in json.dumps(m).lower()
                                for m in outcomes), outcomes)
            self.assertEqual((probe.repo / 'sample.py').read_text(), 'value = 2\n')

    def test_invalid_and_truncated_answers_never_become_successful_empty_reviews(self):
        payloads = [('not JSON', 'stop'), ('{"status":"complete","comments":[]}', 'length'),
            (json.dumps(dict(status='complete', comments=[dict(path='../secret', start_line=1,
                end_line=1, content='bad path')])), 'stop'),
            (json.dumps(dict(status='complete', comments=[dict(path='sample.py', start_line=1,
                end_line=99, content='out of range')])), 'stop')]
        for text, reason in payloads:
            with self.subTest(reason=reason, text=text), serve(lambda body, index: (dict(content=text), reason)) as model, case(model) as probe:
                result = probe.review()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((probe.run / 'results/local.case.json').exists())
                self.assertEqual(len(model.requests), 1)
                recovered = probe.stage('recover', '--instance', 'local.case')
                self.assertNotEqual(recovered.returncode, 0)
                self.assertEqual(len(model.requests), 1)

    def test_retryable_http_and_context_overflow_do_not_retry(self):
        for status, code in [(503, 'server_error'), (400, 'context_length_exceeded')]:
            reply = dict(http_status=status, http_error={'error': {'message': code, 'code': code, 'type': code}})
            with self.subTest(status=status), serve(lambda body, index: (reply, 'stop')) as model, case(model) as probe:
                result = probe.review()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((probe.run / 'results/local.case.json').exists())
                self.assertEqual(len(model.requests), 1)
                if status == 400:
                    events = [json.loads(line) for line in (probe.run / 'raw/local.case.stdout.jsonl').read_text().splitlines()]
                    ends = [row for row in events if row.get('phase') == 'turn_end']
                    self.assertEqual(ends[-1]['reason']['error']['code'], 'CONTEXT_WINDOW_EXCEEDED')
                self.assertNotEqual(probe.review().returncode, 0)
                self.assertEqual(len(model.requests), 1)

    def test_unknown_usage_is_not_reported_as_zero_or_full_coverage(self):
        reply = dict(content='{"status":"complete","comments":[]}')
        with serve(lambda body, index: (reply, 'stop')) as model, case(model) as probe:
            model.usage = {'unrecognized': 10}
            result = probe.review()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            answer = json.loads((probe.run / 'results/local.case.json').read_text())
            self.assertEqual(answer['review']['summary'], {})
            self.assertEqual(answer['review']['usage_coverage'], {'known_requests': 0, 'requests': 1})

    def test_deadline_kills_real_dsh_and_does_not_retry_the_case(self):
        def slow(body, index):
            time.sleep(70)
            return dict(content='{"status":"complete","comments":[]}'), 'stop'
        with serve(slow) as model, case(model, minutes=1) as probe:
            started = time.monotonic()
            result = probe.review()
            elapsed = time.monotonic() - started
            self.assertNotEqual(result.returncode, 0)
            record = json.loads((probe.run / 'raw/local.case.execution.json').read_text())
            self.assertEqual(record['status'], 'timed_out')
            self.assertGreaterEqual(elapsed, 59)
            self.assertLess(elapsed, 80)
            self.assertIn('cleanup_seconds', record)
            with self.assertRaises(ProcessLookupError):
                os.killpg(record['pid'], 0)
            self.assertEqual(len(model.requests), 1)
            self.assertFalse((probe.run / 'results/local.case.json').exists())
