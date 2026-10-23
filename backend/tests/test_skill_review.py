"""Imported Skill selection through authenticated API and the durable queue."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

import test_skill_assets as assets
from webui.queue import DurableDashboard
from webui.service import save
from webui.wsgi import create_app
from dsh_probe import case
from tools.dsh_local_model import serve

MAIN = assets.MAIN
package = assets.package


class SkillReviewApiTests(unittest.TestCase):
    request = assets.SkillAssetApiTests.request
    upload = assets.SkillAssetApiTests.upload

    def setUp(self):
        assets.SkillAssetApiTests.setUp(self)
        root = Path(self.temp.name)
        (root / 'data').mkdir()
        source = Path(__file__).resolve().parents[2] / 'data/pilot1.jsonl'
        (root / 'data/pilot1.jsonl').write_bytes(source.read_bytes())
        save(root / 'model.local.json', dict(LCR_LLM_MODEL='local-review',
             LCR_LLM_TOKEN='local-only', LCR_LLM_BASE_URL='http://127.0.0.1:9/v1',
             JUDGE_MODEL='local-judge', JUDGE_API_KEY='judge-only',
             JUDGE_BASE_URL='http://127.0.0.1:9/v1'))
        self.dashboard = DurableDashboard(root)
        self.client = create_app(self.dashboard).test_client()

    def create(self, **changes):
        request = dict(action='create', suite='aacr', dataset='pilot1.jsonl',
                       name='selected-skill', reviewer='dsh_skill', skill_mode='skill',
                       evaluator='original')
        request.update(changes)
        return self.request('/api/jobs', 'POST', json=request)

    def test_selected_version_and_complete_identity_survive_queue_restart(self):
        self.assertEqual(self.upload(package())['status'], 'succeeded')
        version = self.request('/api/skills').json['versions'][0]
        reply = self.create(skill_version=version['id'])
        self.assertEqual(reply.status_code, 202, reply.json)
        changed = package([('SKILL.md', MAIN), ('references/rules.txt', b'new version')])
        self.assertEqual(self.upload(changed)['status'], 'succeeded')
        restarted = DurableDashboard(Path(self.temp.name))
        job, queued, frozen = restarted.queue.claim()
        self.assertEqual(job['id'], reply.json['job_id'])
        self.assertEqual(queued['skill_version'], version['id'])
        self.assertEqual(frozen['skill_mode'], 'skill')
        self.assertEqual(frozen['skill_version'], version['id'])
        self.assertEqual(frozen['skill_identity']['files'], version['files'])
        self.assertEqual(frozen['skill_identity']['archive_sha256'], version['archive_sha256'])
        self.assertEqual(frozen['evaluator'], 'original')
        self.assertFalse(frozen.get('start_after_create', False))
        self.assertIsNone(restarted.queue.claim())
        self.assertEqual(len(restarted.queue.jobs()), 1)
        self.assertNotIn('local-only', json.dumps(restarted.queue.jobs()))

    def test_invalid_inconsistent_or_corrupt_versions_never_enter_queue(self):
        self.upload(package())
        version = self.request('/api/skills').json['versions'][0]['id']
        for changes in ({}, {'skill_version': 'f' * 64}, {'skill_version': version, 'skill_mode': 'baseline'},
                        {'skill_version': version, 'reviewer': 'lcr'}, {'skill_mode': ['skill']}):
            reply = self.create(**changes)
            self.assertEqual(reply.status_code, 400, reply.json)
        path = self.dashboard.data / 'skill-assets/versions' / version / 'content/references/rules.txt'
        path.write_bytes(b'corrupted')
        self.assertEqual(self.create(skill_version=version).status_code, 400)
        self.assertEqual(self.dashboard.queue.jobs(), {})

    def test_frozen_content_or_identity_damage_stops_actual_dsh_before_model_calls(self):
        self.upload(package())
        version = self.request('/api/skills').json['versions'][0]['id']
        with serve() as model, case(model, skill_version=version, assets_root=self.temp.name) as probe:
            for name in ('content/references/rules.txt', 'manifest.json'):
                path = probe.run / 'skill' / name
                saved = path.read_bytes()
                path.write_bytes(b'corrupted')
                result = probe.review()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue((probe.run / 'dsh-system-error.json').exists())
                self.assertEqual(len(model.requests), 0)
                path.write_bytes(saved)
            config = probe.run / 'run-config.json'
            original = config.read_bytes()
            changed = json.loads(original)
            changed.update(skill_mode='baseline')
            changed.pop('skill_version')
            changed.pop('skill_identity')
            config.write_text(json.dumps(changed))
            self.assertNotEqual(probe.review().returncode, 0)
            self.assertEqual(len(model.requests), 0)
            config.write_bytes(original)
            self.assertFalse((probe.run / 'results/local.case.json').exists())

    def test_real_worker_initializes_bound_queue_version_and_rejects_later_corruption(self):
        model = self.enterContext(serve())
        save(Path(self.temp.name) / 'model.local.json', dict(LCR_LLM_MODEL='local-review',
             LCR_LLM_TOKEN='local-only', LCR_LLM_BASE_URL=model.url,
             JUDGE_MODEL='local-judge', JUDGE_API_KEY='judge-only', JUDGE_BASE_URL=model.url))
        entries = [('SKILL.md', MAIN), ('references/rules.txt', b'original')]
        entries += [(f'references/extra-{index:04d}-' + 'x' * 70 + '.txt', b'resource')
                    for index in range(900)]
        self.upload(package(entries))
        first = self.request('/api/skills').json['versions'][0]
        saved = self.create(skill_version=first['id']).json
        changed = package([('SKILL.md', MAIN), ('references/rules.txt', b'changed')])
        self.upload(changed)
        second = next(v for v in self.request('/api/skills').json['versions'] if v['id'] != first['id'])
        broken = self.create(skill_version=second['id'], name='corrupt-queued').json
        content = self.dashboard.data / 'skill-assets/versions' / second['id'] / 'content/references/rules.txt'
        content.write_bytes(b'broken after enqueue')
        log = Path(self.temp.name) / 'worker.log'
        with log.open('w') as output:
            worker = subprocess.Popen([sys.executable, '-m', 'webui.worker'], env=dict(os.environ),
                                      stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 60
                statuses = {}
                while time.monotonic() < deadline:
                    jobs = self.request('/api/state').json['jobs']
                    statuses = {j['id']: j['status'] for j in jobs}
                    if all(statuses.get(j) in {'succeeded', 'failed'} for j in (saved['job_id'], broken['job_id'])):
                        break
                    time.sleep(.1)
                self.assertEqual(statuses.get(saved['job_id']), 'succeeded', log.read_text())
                self.assertEqual(statuses.get(broken['job_id']), 'failed', log.read_text())
                cfg = self.request(f"/api/artifact?key={saved['key']}&path=skill/manifest.json").json
                frozen = json.loads(cfg['text'])
                self.assertEqual(frozen['id'], first['id'])
                self.assertEqual(frozen['files'], first['files'])
                self.assertEqual(next(r for r in self.request('/api/state').json['runs']
                                      if r['key'] == saved['key'])['skill_version'], first['id'])
                self.assertFalse((self.dashboard.run_dir(broken['key']) / 'run-config.json').exists())
                self.assertFalse(list((self.dashboard.data / 'runs').glob('*/raw/*.requests.json')))
                self.assertEqual(len(jobs), 2)
                self.assertEqual(model.requests, [])
            finally:
                worker.terminate()
                worker.wait(timeout=15)

    def test_actual_dsh_injects_full_original_skill_and_reads_bound_resources(self):
        main = MAIN + b'Unchanged review policy.\r\n' * 1200 + b'END_OF_ORIGINAL_SKILL\n'
        self.assertEqual(self.upload(package([('SKILL.md', main),
                         ('references/rules.txt', b'BOUND_RESOURCE_ORIGINAL\r\n'),
                         ('scripts/unused.sh', b'exit 99\n')]))['status'], 'succeeded')
        version = self.request('/api/skills').json['versions'][0]

        def response(body, index):
            if index == 1:
                return dict(content=None, tool_calls=[dict(id='rules', type='function',
                    function=dict(name='read', arguments=json.dumps(dict(
                        file_path='/skill/references/rules.txt'))))]), 'tool_calls'
            return dict(content='{"status":"complete","comments":[]}'), 'stop'

        with serve(response) as model, case(model, skill_version=version['id'],
                                           assets_root=self.temp.name) as probe:
            result = probe.review()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            texts = [m.get('content', '') for m in model.requests[0]['messages']]
            self.assertTrue(any(main.decode() in text for text in texts))
            self.assertIn('BOUND_RESOURCE_ORIGINAL', json.dumps(model.requests[-1]))
            result = json.loads((probe.run / 'results/local.case.json').read_text())
            self.assertEqual(result['skill']['version'], version['id'])
            self.assertTrue(result['skill']['loaded'])
            self.assertEqual(result['skill']['evidence'][0]['form'], 'explicit_injection')
            self.assertEqual(len(model.requests), 2)
            self.assertEqual((probe.run / 'skill/content/SKILL.md').read_bytes(), main)
            saved = (probe.run / 'results/local.case.json').read_bytes()
            self.assertEqual(probe.stage('recover', '--instance', 'local.case').returncode, 0)
            self.assertEqual((probe.run / 'results/local.case.json').read_bytes(), saved)
            self.assertEqual(len(model.requests), 2)
            target = probe.run / 'results/local.case.json'
            target.unlink()
            inputs = probe.run / 'raw/local.case.model-inputs.json'
            original = inputs.read_bytes()
            inputs.write_bytes(original.replace(b'END_OF_ORIGINAL_SKILL', b'TRUNCATED_SKILL'))
            self.assertNotEqual(probe.stage('recover', '--instance', 'local.case').returncode, 0)
            self.assertFalse(target.exists())
            inputs.write_bytes(original)
            self.assertEqual(probe.stage('recover', '--instance', 'local.case').returncode, 0)
            self.assertEqual(target.read_bytes(), saved)
            self.assertEqual(len(model.requests), 2)

    def test_dotted_case_identity_and_nested_skill_preserve_actual_loading_evidence(self):
        self.upload(package([('package&rules/SKILL.md', MAIN),
                             ('package&rules/references/rules.txt', b'original')]))
        version = self.request('/api/skills').json['versions'][0]['id']
        reply = dict(content='{"status":"complete","comments":[]}')
        with serve(lambda body, index: (reply, 'stop')) as model, case(
                model, skill_version=version, assets_root=self.temp.name,
                instance_id='special.requests.json.case') as probe:
            result = probe.review()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            target = probe.run / 'results/special.requests.json.case.json'
            self.assertTrue(json.loads(target.read_text())['skill']['loaded'])
            cfg = json.loads((probe.run / 'run-config.json').read_text())
            self.assertEqual(set(cfg['dsh_policy']['tools']), {'read', 'glob', 'grep', 'skill'})
            self.assertEqual(len(model.requests), 1)

    def test_case_copy_damage_during_actual_execution_stops_without_a_quality_answer(self):
        self.upload(package())
        version = self.request('/api/skills').json['versions'][0]['id']
        active = {}

        def response(body, index):
            execution = active['probe'].run / 'raw/local.case.execution.json'
            command = json.loads(execution.read_text())['command_without_credentials']
            content = Path(command[command.index('/skill') - 1])
            (content / 'references/rules.txt').write_bytes(b'host fault during execution')
            return dict(content='{"status":"complete","comments":[]}'), 'stop'

        with serve(response) as model, case(model, skill_version=version, assets_root=self.temp.name) as probe:
            active['probe'] = probe
            result = probe.review()
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((probe.run / 'dsh-system-error.json').exists())
            self.assertFalse((probe.run / 'results/local.case.json').exists())
            self.assertEqual(len(model.requests), 1)
            self.assertNotEqual(probe.stage('recover', '--instance', 'local.case').returncode, 0)
            self.assertFalse((probe.run / 'results/local.case.json').exists())
            self.assertEqual(len(model.requests), 1)

    def test_failed_case_does_not_hide_changed_frozen_skill_conditions(self):
        self.upload(package())
        version = self.request('/api/skills').json['versions'][0]['id']
        active = {}

        def response(body, index):
            content = active['probe'].run / 'skill/content/references/rules.txt'
            content.write_bytes(b'host fault while failed request is active')
            return dict(http_status=503, http_error={'error': {'message': 'injected failure'}}), 'stop'

        with serve(response) as model, case(model, skill_version=version, assets_root=self.temp.name) as probe:
            active['probe'] = probe
            self.assertNotEqual(probe.review().returncode, 0)
            self.assertTrue((probe.run / 'dsh-system-error.json').exists())
            self.assertFalse((probe.run / 'results/local.case.json').exists())
            self.assertEqual(len(model.requests), 1)
            self.assertNotEqual(probe.stage('recover', '--instance', 'local.case').returncode, 0)
            self.assertFalse((probe.run / 'results/local.case.json').exists())
            self.assertEqual(len(model.requests), 1)

    def test_native_skill_tool_evidence_survives_without_injection_but_not_truncation(self):
        self.upload(package())
        version = self.request('/api/skills').json['versions'][0]['id']

        def response(body, index):
            if index == 1:
                return dict(content=None, tool_calls=[dict(id='load', type='function',
                    function=dict(name='skill', arguments='{"name":"candidate-skill"}'))]), 'tool_calls'
            return dict(content='{"status":"complete","comments":[]}'), 'stop'

        with serve(response) as model, case(model, skill_version=version, assets_root=self.temp.name) as probe:
            result = probe.review()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            target = probe.run / 'results/local.case.json'
            target.unlink()
            sessions = list((probe.run / 'raw').glob('*.session-*.jsonl'))
            for path in sessions:
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                rows = [r for r in rows if r.get('data', {}).get('source', {}).get('kind') != 'skill-invocation']
                path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            recovered = probe.stage('recover', '--instance', 'local.case')
            self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
            answer = json.loads(target.read_text())
            self.assertEqual({p['form'] for p in answer['skill']['evidence']}, {'tool_load'})
            target.unlink()
            for path in sessions:
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                for row in rows:
                    if row.get('type') == 'tool/result':
                        row['data']['message']['content'][0]['text'] = '<skill_content name="candidate-skill">truncated'
                path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            self.assertNotEqual(probe.stage('recover', '--instance', 'local.case').returncode, 0)
            self.assertFalse(target.exists())
            self.assertEqual(len(model.requests), 2)

    def test_denied_required_capabilities_are_unsupported_not_quality_answers(self):
        self.upload(package())
        version = self.request('/api/skills').json['versions'][0]['id']

        def response(body, index):
            if index == 1:
                calls = [('bash', {'command': '/skill/scripts/inspect.sh'}),
                         ('subagent', {'prompt': 'required delegation'}),
                         ('web_fetch', {'url': 'https://example.invalid/required'}),
                         ('write', {'file_path': '/work/sample.py', 'content': 'bad'})]
                return dict(content=None, tool_calls=[dict(id=str(i), type='function',
                    function=dict(name=name, arguments=json.dumps(args)))
                    for i, (name, args) in enumerate(calls)]), 'tool_calls'
            return dict(content='{"status":"complete","comments":[]}'), 'stop'

        with serve(response) as model, case(model, skill_version=version, assets_root=self.temp.name) as probe:
            result = probe.review()
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((probe.run / 'results/local.case.json').exists())
            support = json.loads((probe.run / 'raw/local.case.skill-support.json').read_text())
            self.assertEqual(support['status'], 'unsupported')
            self.assertEqual({entry['name'] for entry in support['basis']}, {'bash', 'subagent', 'web_fetch', 'write'})
            status = probe.stage('status')
            self.assertEqual(json.loads(status.stdout)[0]['status'], 'unsupported')
            self.assertEqual(len(model.requests), 2)
            self.assertEqual((probe.repo / 'sample.py').read_text(), 'value = 2\n')

    def test_skill_failures_never_become_complete_answers_or_trigger_hidden_retries(self):
        self.upload(package())
        version = self.request('/api/skills').json['versions'][0]['id']
        failures = [
            (dict(content='{"status":"unsupported","reason":"Must execute scripts/inspect.sh"}'), 'stop'),
            (dict(content='not JSON'), 'stop'),
            (dict(content='{"status":"complete","comments":[]}'), 'length'),
            (dict(content=json.dumps(dict(status='complete', comments=[dict(
                path='sample.py', start_line=1, end_line=99, content='out of bounds')]))), 'stop'),
            (dict(http_status=503, http_error={'error': {'code': 'server_error', 'message': 'local injected fault'}}), 'stop'),
        ]
        for reply, reason in failures:
            with self.subTest(reply=reply), serve(lambda body, index: (reply, reason)) as model, case(
                    model, skill_version=version, assets_root=self.temp.name) as probe:
                result = probe.review()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse((probe.run / 'results/local.case.json').exists())
                self.assertEqual(len(model.requests), 1)
                self.assertNotEqual(probe.stage('recover', '--instance', 'local.case').returncode, 0)
                self.assertEqual(len(model.requests), 1)
                self.assertNotEqual(probe.review().returncode, 0)
                self.assertEqual(len(model.requests), 1)

    def test_required_read_scope_denial_cannot_be_silently_scored_as_empty(self):
        self.upload(package())
        version = self.request('/api/skills').json['versions'][0]['id']

        targets = [('/runtime/node_modules/@deepseek-ai/dsh/README.md', True),
                   ('/work/ordinary-missing-file.txt', False)]
        for target, denied in targets:
            def response(body, index):
                if index == 1:
                    return dict(content=None, tool_calls=[dict(id='required-context', type='function',
                        function=dict(name='read', arguments=json.dumps(dict(file_path=target))))]), 'tool_calls'
                return dict(content='{"status":"complete","comments":[]}'), 'stop'

            with self.subTest(target=target), serve(response) as model, case(
                    model, skill_version=version, assets_root=self.temp.name) as probe:
                result = probe.review()
                self.assertEqual(result.returncode == 0, not denied, result.stdout + result.stderr)
                self.assertEqual((probe.run / 'results/local.case.json').exists(), not denied)
                support = json.loads((probe.run / 'raw/local.case.skill-support.json').read_text())
                self.assertEqual(support['status'], 'unsupported' if denied else 'supported')
                self.assertEqual([r['name'] for r in support['basis']], ['read'] if denied else [])
                self.assertEqual(len(model.requests), 2)
