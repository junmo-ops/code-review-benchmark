"""Lifecycle evidence through public CLI and ordinary authenticated batch APIs."""
import json
import os
from pathlib import Path
import signal
import threading
import time
import unittest
from unittest.mock import patch

from dsh_probe import case
from dsh_lifecycle_fixture import LifecycleFixture
from local import descendants
from runner.storage import save
from tools.dsh_local_model import serve


class DSHLifecycleTests(unittest.TestCase):
    def test_between_stage_frozen_configuration_change_stops_before_any_request(self):
        with serve() as model, case(model) as probe:
            config = probe.run / 'run-config.json'
            value = json.loads(config.read_text())
            value['timeout_minutes'] = 9
            config.write_text(json.dumps(value))
            self.assertNotEqual(probe.review().returncode, 0)
            self.assertEqual(model.requests, [])
            self.assertTrue((probe.run / 'dsh-system-error.json').exists())

    def test_recovery_cannot_reverse_a_frozen_condition_stop_after_original_config_is_restored(self):
        active = {}

        def response(body, index):
            config = active['probe'].run / 'run-config.json'
            value = json.loads(config.read_text())
            value['timeout_minutes'] = 9
            config.write_text(json.dumps(value))
            return dict(content='{"status":"complete","comments":[]}'), 'stop'

        with serve(response) as model, case(model) as probe:
            active['probe'] = probe
            config = probe.run / 'run-config.json'
            original = config.read_bytes()
            self.assertNotEqual(probe.review().returncode, 0)
            self.assertTrue((probe.run / 'dsh-system-error.json').exists())
            self.assertFalse((probe.run / 'results/local.case.json').exists())
            config.write_bytes(original)
            self.assertNotEqual(probe.stage('recover', '--instance', 'local.case').returncode, 0)
            self.assertFalse((probe.run / 'results/local.case.json').exists())
            self.assertEqual(len(model.requests), 1)

    def test_malformed_review_keeps_failure_and_elapsed_time_in_status_and_report(self):
        with serve(lambda body, index: (dict(content='not JSON'), 'stop')) as model, case(model) as probe:
            result = probe.review()
            self.assertNotEqual(result.returncode, 0)
            status = json.loads(probe.stage('status').stdout)
            self.assertEqual(status[0]['status'], 'failed')
            self.assertNotEqual(probe.stage('eval').returncode, 0)
            report = (probe.run / 'run-summary.zh-CN.md').read_text()
            self.assertIn('完整答卷 0/1', report)
            self.assertIn('失败 1', report)
            self.assertIn('检视秒数', report)
            self.assertEqual(len(model.requests), 1)


def controlled_response(body, index):
    if body['model'] == 'dsh-judge':
        from tools.dsh_local_model import completion
        return completion(body, index)
    return dict(content=json.dumps(dict(status='complete', comments=[dict(
        path='sample.py', start_line=1, end_line=1, content='本地确定性缺陷意见，仅验证测评流程。')]))), 'stop'


class DSHQueueLifecycleTests(unittest.TestCase):
    def test_fifty_cases_keep_failures_and_timeout_then_resume_only_selected_judge_protocol(self):
        judge_faults = []

        def response(body, index):
            if body['model'] == 'dsh-judge' and len(judge_faults) < 2:
                judge_faults.append(time.monotonic())
                status = 503 if len(judge_faults) == 1 else 400
                return dict(http_status=status, http_error={'error': {'message': 'injected judge failure'}}), 'stop'
            if body['model'] == 'dsh-review':
                identifier = json.loads(body['messages'][0]['content'])['instance_id']
                if identifier == 'scale-001':
                    return dict(http_status=503, http_error={'error': {'message': 'injected review failure'}}), 'stop'
                if identifier == 'scale-002':
                    return dict(content='malformed answer'), 'stop'
                if identifier == 'scale-003':
                    time.sleep(70)
            return controlled_response(body, index)

        model = self.enterContext(serve(response))
        model.usage = {'unrecognized': 10}
        fixture = LifecycleFixture(self, model)
        created = fixture.create('controlled-fifty-faults', timeout_minutes=1)
        fixture.wait(lambda: fixture.job(created)['status'] == 'failed', 1000)
        run = fixture.root / 'runs' / created['key']
        cases = next(r for r in fixture.get('/api/state')['runs'] if r['key'] == created['key'])['cases']
        statuses = {row['id']: row['status'] for row in cases}
        self.assertEqual(len(statuses), 50)
        self.assertEqual(statuses['scale-001'], 'failed')
        self.assertEqual(statuses['scale-002'], 'failed')
        self.assertEqual(statuses['scale-003'], 'timed_out')
        self.assertEqual(sum(status == 'complete' for status in statuses.values()), 47)
        self.assertEqual(len([r for r in model.requests if r['model'] == 'dsh-review']), 50)
        self.check_submitted_ranges(run, model)
        self.assertGreaterEqual(judge_faults[1] - judge_faults[0], 9.5)
        self.check_cleanup_records(run)
        before = (run / 'results/scale-000.json').read_bytes()
        scored = fixture.post('/api/jobs', dict(action='eval', key=created['key']))
        fixture.wait(lambda: fixture.job(scored)['status'] in {'succeeded', 'failed'}, 120)
        self.assertEqual(fixture.job(scored)['status'], 'succeeded')
        current = next(r for r in fixture.get('/api/state')['runs'] if r['key'] == created['key'])
        self.assertEqual(current['scored_cases'], 47)
        self.assertEqual({row['id'] for row in current['cases'] if row['scored']},
                         set(statuses) - {'scale-001', 'scale-002', 'scale-003'})
        audit = json.loads((run / 'judge-audit.json').read_text())
        self.assertEqual(audit['eval_rounds'], 3)
        self.assertEqual(audit['transport'], dict(request_timeout_seconds=180, retry_delays_seconds=[10, 30]))
        self.assertEqual(len(audit['requests']), 143)
        report = (run / 'report.zh-CN.md').read_text()
        self.assertIn('完整答卷 47/50；失败 2；超时 1；实际评分 47/50', report)
        answer = json.loads(before)
        self.assertEqual(answer['review']['summary'], {})
        self.assertEqual(answer['review']['usage_coverage'], dict(known_requests=0, requests=1))
        self.check_zero_call_recovery(fixture, created, run, model, before)

    def check_submitted_ranges(self, run, model):
        rows = [json.loads(line) for line in (run / 'dataset.jsonl').read_text().splitlines()]
        expected = [(row['instance_id'], row['repo'], row['base_commit'], row['head_commit']) for row in rows]
        submitted = [json.loads(body['messages'][0]['content']) for body in model.requests if body['model'] == 'dsh-review']
        actual = [(row['instance_id'], row['repo'], row['base_commit'], row['head_commit']) for row in submitted]
        self.assertEqual(actual, expected)

    def check_cleanup_records(self, run):
        records = [json.loads(path.read_text()) for path in (run / 'raw').glob('*.execution.json')]
        self.assertEqual(len(records), 50)
        for record in records:
            self.assertEqual(record['cleanup_status'], 'complete')
            command = record['command_without_credentials']
            workspace = Path(command[command.index('/work') - 1]).parent
            self.assertFalse(workspace.exists(), str(workspace))
        timeout = json.loads((run / 'raw/scale-003.execution.json').read_text())
        self.assertGreaterEqual(timeout['supervised_seconds'], 59)
        self.assertLess(timeout['supervised_seconds'], 65)

    def check_zero_call_recovery(self, fixture, created, run, model, before):
        requests = len(model.requests)
        target = run / 'results/scale-000.json'
        for remove in (False, True):
            if remove:
                target.unlink()
            recovered = fixture.post('/api/jobs', dict(action='recover', key=created['key'], task='scale-000'))
            fixture.wait(lambda: fixture.job(recovered)['status'] == 'succeeded')
            self.assertEqual(target.read_bytes(), before)
        for identifier in ('scale-001', 'scale-002', 'scale-003'):
            recovered = fixture.post('/api/jobs', dict(action='recover', key=created['key'], task=identifier))
            fixture.wait(lambda: fixture.job(recovered)['status'] == 'failed')
            self.assertFalse((run / 'results' / (identifier + '.json')).exists())
        self.assertEqual(len(model.requests), requests)
        reply = fixture.request('/api/jobs', 'POST', json=dict(action='review', key=created['key'],
                                task='scale-001', retry=True))
        self.assertEqual(reply.status_code, 400)

    def test_cleanup_confirmation_failure_stops_entire_fifty_case_batch(self):
        self.enterContext(patch.dict(os.environ, {'DSH_CONTROLLED_CLEANUP_FAIL': '1'}))
        model = self.enterContext(serve(controlled_response))
        fixture = LifecycleFixture(self, model)
        created = fixture.create('cleanup-denial')
        fixture.wait(lambda: fixture.job(created)['status'] == 'failed', 60)
        run = fixture.root / 'runs' / created['key']
        self.assertTrue((run / 'dsh-system-error.json').exists())
        self.assertEqual(len(list((run / 'raw').glob('*.execution.json'))), 1)
        self.assertFalse(list((run / 'results').glob('*.json')))
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(len(json.loads((run / 'run-config.json').read_text())['selected_instances']), 50)

    def test_frozen_scorer_identity_change_stops_before_second_review_or_evaluation(self):
        self.enterContext(patch.dict(os.environ, {'DSH_CONTROLLED_SUITE_CHANGED': '1'}))
        model = self.enterContext(serve(controlled_response))
        fixture = LifecycleFixture(self, model)
        created = fixture.create('scorer-identity-change')
        fixture.wait(lambda: fixture.job(created)['status'] == 'failed', 90)
        run = fixture.root / 'runs' / created['key']
        log = (fixture.app.state / 'jobs' / (created['job_id'] + '.log')).read_text()
        self.assertTrue((run / 'dsh-system-error.json').exists())
        self.assertEqual(log.count('--instance scale-'), 1)
        self.assertEqual(fixture.job(created)['step'], 'review · scale-000')
        self.assertFalse(list((run / 'results').glob('*.json')))
        self.assertEqual(model.requests, [])
        self.assertEqual(len(json.loads((run / 'run-config.json').read_text())['selected_instances']), 50)

    def blocking_fixture(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def response(body, index):
            if body['model'] == 'dsh-review' and json.loads(body['messages'][0]['content'])['instance_id'] == 'scale-001':
                release.wait(90)
            return controlled_response(body, index)

        model = self.enterContext(serve(response))
        fixture = LifecycleFixture(self, model)
        created = fixture.create('controlled-stopped')
        fixture.wait(lambda: len(model.requests) == 2, 60)
        run = fixture.root / 'runs' / created['key']
        record = json.loads((run / 'raw/scale-001.execution.json').read_text())
        observed = descendants(record['pid'])
        self.assertGreaterEqual(len(observed), 3, 'executor and its actual descendant must both be observed')
        command = record['command_without_credentials']
        work = Path(command[command.index('/work') - 1]).parent
        save(fixture.root / 'process-observation-before.json', dict(
            at=time.time(), pids=sorted(observed), workspace=str(work), workspace_exists=work.exists(),
            requests=len(model.requests), execution=record))
        return fixture, created, model, run, observed, work

    def assert_reclaimed(self, fixture, run, observed, work):
        def reclaimed():
            record = json.loads((run / 'raw/scale-001.execution.json').read_text())
            return record['status'] == 'interrupted' and record.get('cleanup_status') == 'complete' and not work.exists()
        fixture.wait(reclaimed, 20)
        for pid in observed:
            stat = Path(f'/proc/{pid}/stat')
            if stat.exists():
                self.assertIn(stat.read_text().rsplit(')', 1)[1].split()[0], ('Z', 'X'))
        save(fixture.root / 'process-observation-after.json', dict(
            at=time.time(), live_pids=[pid for pid in observed if live(pid)],
            workspace_exists=work.exists(), execution=json.loads((run / 'raw/scale-001.execution.json').read_text())))

    def test_cancel_full_frozen_list_and_queued_job_reclaims_tree_without_more_calls(self):
        fixture, created, model, run, observed, work = self.blocking_fixture()
        queued = fixture.create('controlled-queued')
        self.assertEqual(fixture.job(queued)['status'], 'queued')
        fixture.post('/api/stop', dict(job=queued['job_id']))
        fixture.post('/api/stop', dict(job=created['job_id']))
        fixture.wait(lambda: fixture.job(created)['status'] == 'cancelled')
        self.assert_reclaimed(fixture, run, observed, work)
        self.assertEqual(fixture.job(queued)['status'], 'cancelled')
        self.assertEqual(len(json.loads((run / 'run-config.json').read_text())['selected_instances']), 50)
        self.assertEqual(len(list((run / 'results').glob('*.json'))), 1)
        time.sleep(1)
        self.assertEqual(len(model.requests), 2)
        self.assertFalse(fixture.get('/api/state')['recovery_required'])

    def test_worker_death_reclaims_active_tree_and_restart_never_replays_review(self):
        fixture, created, model, run, observed, work = self.blocking_fixture()
        runner_pid = fixture.job(created)['pid']
        self.addCleanup(stop_group, runner_pid)
        fixture.worker.kill()
        fixture.worker.wait(timeout=5)
        self.assert_reclaimed(fixture, run, observed, work)
        fixture.start_worker()
        fixture.wait(lambda: fixture.job(created)['status'] == 'interrupted')
        self.assertTrue(fixture.get('/api/state')['recovery_required'])
        before = (run / 'results/scale-000.json').read_bytes()
        fixture.post('/api/recovery', dict(confirmed=True))
        time.sleep(3)
        self.assertEqual(len(model.requests), 2)
        self.assertEqual((run / 'results/scale-000.json').read_bytes(), before)
        self.assertIsNone(fixture.app.queue.claim())


def stop_group(pid):
    try:
        os.killpg(pid, signal.SIGINT)
    except ProcessLookupError:
        pass


def live(pid):
    try:
        state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
    except FileNotFoundError:
        return False
    return state not in {'Z', 'X'}
