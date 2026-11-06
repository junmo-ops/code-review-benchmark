"""Execute planned jobs and persist their lifecycle independently of HTTP."""
import os
import subprocess
import threading
import time
from .files import now


class JobExecutor:
    def __init__(self, context):
        self.context = context

    def _continue_partial_review(self, job, steps, index, stage, code, timed_out=False):
        # AACR's evaluator can score complete answers while explicitly excluding failed cases.
        if (self.context.run_dir(job['key'])/'dsh-system-error.json').exists():
            return False
        if (code == 0 and not timed_out) or stage != 'review' or job['action'] not in {'workflow', 'create'}:
            return False
        if job['action'] == 'create' and not job.get('start_after_create'):
            return False
        if self.context.config(job['key']).get('suite', {}).get('id') != 'aacr':
            return False
        return any(next_stage == 'eval' for next_stage, _, _ in steps[index + 1:])


    def _execute_stage(self, job_id, job, request, stage, task, init, log):
        cmd, env, limit = self.context.command(job['key'], stage, job['profile'], task,
                                       bool(request.get('retry')), init)
        env['PYTHONUTF8'] = '1'
        log.write(f'\n[{now()}] {stage + (" · " + task if task else "")}\n'
                  + self.context.redact(' '.join(cmd)) + '\n')
        process = None
        reader = None
        try:
            process_options = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt'
                               else {'start_new_session': True})
            process = subprocess.Popen(cmd, cwd=self.context.root, env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                       errors='replace', **process_options)
            self.context.update(job_id, pid=process.pid)
            stream = process.stdout

            def output():
                try:
                    for line in stream:
                        log.write(self.context.redact(line))
                except (OSError, ValueError):
                    # The main process exit code remains authoritative if a child retains the pipe.
                    pass

            reader = threading.Thread(target=output, daemon=True)
            reader.start()
            deadline = time.monotonic() + limit + 30
            timed_out = False
            while process.poll() is None:
                cancelled = self.context.cancelled.wait(.15)
                deadline_reached = time.monotonic() > deadline
                if cancelled or deadline_reached:
                    from local import terminate
                    terminate(process)
                    timed_out = deadline_reached and not cancelled
                    break
            reader.join(timeout=10)
            if reader.is_alive():
                log.write('主命令已退出；仍有子进程持有日志流，按主命令退出码继续。\n')
            else:
                stream.close()
            return process.returncode, timed_out
        finally:
            if reader and reader.is_alive():
                reader.join(timeout=1)
            if process is not None and process.poll() is None:
                from local import terminate
                terminate(process)


    def _run_work_stage(self, job_id, job, request, steps, index, stage, task, init, log):
        label = stage + (' · ' + task if task else '')
        self.context.update(job_id, step=label, step_started_at=now(), completed_steps=index, message="正在执行")
        from runner.diagnostics import phase, event
        with phase('benchmark.stage', job_id=job_id, key=job['key'], stage=stage):
            code, timed_out = self._execute_stage(job_id, job, request, stage, task, init, log)
        event('benchmark.stage.result', job_id=job_id, stage=stage, exit_code=code,
              status='timeout' if timed_out else 'exited')
        self.context.update(job_id, exit_code=code)
        if self.context.cancelled.is_set():
            return 'cancelled'
        if code != 0 or timed_out:
            if stage == 'comparison':
                raise ValueError('对比实验存在失败或未完成案例，请查看对比报告与日志；不自动重试。')
            if self._continue_partial_review(job, steps, index, stage, code, timed_out):
                reason = '外层时限已到，检视进程已停止' if timed_out else '检视存在未完成案例'
                log.write(f'{reason}；继续评分结构完整的答卷，失败案例将从评分中排除。\n')
                self.context.update(job_id, completed_steps=index+1, message='检视部分失败，正在评分已完成案例')
                return 'partial'
            if timed_out:
                raise ValueError('页面外层执行时限已到，进程已停止，保留日志')
            raise ValueError('本步骤失败，后续步骤未执行。请查看日志；已完成答卷保留。')
        self.context.update(job_id, completed_steps=index+1)
        return 'complete'


    def _prepare_job_repositories(self, job_id, job, spec, log):
        def progress(stage, message, completed=0, total=0, **details):
            if self.context.cancelled.is_set():
                raise ValueError('准备仓库期间收到停止请求')
            log.write(f'[{now()}] {message}\n')
            log.flush()
            self.context.update(job_id, message=message)
        self.context.prepare_case_repositories(spec, job['key'], job['action'], progress=progress)


    def work(self, job_id, request, spec):
        partial_review = False
        log_path = self.context.state/'jobs'/f'{job_id}.log'
        try:
            job = self.context.jobs[job_id]
            steps = self.context.plan(job, request, spec)
            self.context.update(job_id, status='running', started_at=now(), total_steps=len(steps), message='正在执行')
            with log_path.open('a', encoding='utf-8', buffering=1) as log:
                self._prepare_job_repositories(job_id, job, spec, log)
                for index, (stage, task, init) in enumerate(steps):
                    if self.context.cancelled.is_set():
                        break
                    outcome = self._run_work_stage(job_id, job, request, steps, index, stage, task, init, log)
                    if outcome == 'cancelled':
                        break
                    partial_review = partial_review or outcome == 'partial'
                    if stage=='init' and request.get('start_after_create') and not self.context.cancelled.is_set():
                        steps.extend(self.context.plan(dict(job,key=job['key'],action='workflow'),{},None))
                        self.context.update(job_id,total_steps=len(steps))
            message = ('已停止，已写入的结果保留' if self.context.cancelled.is_set() else
                       '评分已完成；部分案例检视失败，请查看案例状态与日志' if partial_review else '执行完成')
            self.context.update(job_id, status='cancelled' if self.context.cancelled.is_set() else 'succeeded', message=message, ended_at=now())
        except BaseException as exc:
            detail = self.context.redact(str(exc)) if isinstance(exc, ValueError) else type(exc).__name__+'；请查看日志和批次原始证据'
            with log_path.open('a', encoding='utf-8') as log:
                log.write('\n'+detail+'\n')
            self.context.update(job_id, status='cancelled' if self.context.cancelled.is_set() else 'failed', message=detail, ended_at=now())
        finally:
            with self.context.lock:
                self.context.active = None

