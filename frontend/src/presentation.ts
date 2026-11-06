import type { ImportTask, Job, Run, Snapshot } from './types';
export const labels: Record<string, string> = {
  pending: '待运行',
  partial: '部分完成',
  partial_scored: '部分评分',
  reviewed: '待评分',
  scored: '已评分',
  complete: '已完成',
  failed: '失败',
  invalid_answer: '答卷无效',
  unsupported: '所需能力不支持',
  queued: '排队中',
  running: '运行中',
  cancelling: '停止中',
  cancelled: '已停止',
  succeeded: '已完成',
  interrupted: '曾中断',
  timed_out: '检视超时',
  unreadable: '需检查',
  simulation_complete: '模拟完成·不计分',
  complete_no_findings: '未检出 · 未修复',
  review_failed: '检视失败',
  score_failed: '评分失败',
  setup_failed: '准备失败',
  reviewing: '检视中',
  scoring: '评分中',
  unfinished_check_required: '未完成 · 需检查',
  recoverable_check_required: '待恢复结果',
  answer_with_item_failures: '答卷含失败项',
  starting: '启动中',
  exited: '执行已结束 · 需检查',
  unknown: '状态未知 · 需检查',
};
export const actions: Record<string, string> = {
  check: '检查准备',
  preview: '预览',
  review: '检视',
  eval: '评分',
  report: '生成报告',
  export: '生成归档',
  recover: '从已有输出恢复结果',
  workflow: '检视并评分',
  resume: '继续未完成案例并评分',
  'compare-martian': 'Martian 对照',
  create: '创建批次',
  comparison: '模型对比实验',
};
export const active = (status: string) => ['queued', 'running', 'cancelling'].includes(status);
export function runStatus(run: Run, jobs: Job[]) {
  const executing = jobs.find((job) => job.key === run.key && active(job.status));
  return executing?.status ?? run.status;
}
export function caseStatus(status: string, job?: Job, caseId?: string) {
  if (status !== 'unfinished_check_required') return status;
  const [stage, selectedCase] = job?.step?.split(' · ') ?? [];
  const reviewing = job && ['running', 'cancelling'].includes(job.status) && stage === 'review';
  return reviewing && (!selectedCase || selectedCase === caseId) ? 'reviewing' : status;
}
export const pct = (value?: number) => (Number.isFinite(value) ? value!.toFixed(3) : '—');
export const stamp = (value?: string) =>
  value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—';
export const availableProfiles = (state: Snapshot) =>
  state.profiles.filter((p) => !p.internal && (p.id !== 'environment' || p.review_ready));
export const suiteName = (state: Snapshot, id: string) =>
  state.catalog.find((s) => s.id === id)?.name ?? id;
export const editable = (state: Snapshot) => state.access_role !== 'viewer';
export const canSubmit = (state: Snapshot) =>
  editable(state) && (state.queue_mode || !state.active) && state.case_assets?.ready !== false;
export function primary(run: Run | undefined, job: Job | undefined, queueMode = false) {
  if (job && active(job.status)) return { tab: 'log', label: '查看运行进度' };
  if (!run) return { tab: 'log', label: '查看创建进度' };
  if (['scored', 'partial_scored', 'complete', 'complete_no_findings'].includes(run.status))
    return { tab: 'report', label: '查看结果' };
  if (canResumeBatch(run, job, queueMode))
    return { action: 'resume', label: run.score_locked_reason ? '继续未完成案例' : '继续未完成案例并评分' };
  if (job && ['failed', 'cancelled', 'interrupted'].includes(job.status))
    return { tab: 'log', label: '查看原因与日志' };
  if (partialReviewReady(run, job)) return { action: 'eval', label: '评分已有答卷' };
  if (['failed', 'partial', 'unreadable'].includes(run.status))
    return { tab: 'log', label: '查看原因与日志' };
  if (run.compatibility_warning) return { tab: 'report', label: '查看历史结果' };
  if (run.status === 'reviewed') return { action: 'eval', label: '开始评分' };
  return { action: 'workflow', label: run.suite === 'ccrab' ? '开始单例验证' : '开始测评' };
}
export function runStages(run: Run) {
  if (run.suite === 'ccrab') return ['check'];
  if (run.suite === 'mcr')
    return ['check', 'review', 'eval', 'report', 'compare-martian', 'export'];
  return ['check', 'preview', 'review', 'recover', 'eval', 'report', 'export'].filter(
    (a) => !run.simulation || !['eval', 'report'].includes(a),
  );
}
export function actionBlocked(state: Snapshot, run: Run, action: string) {
  if (action === 'eval' && run.score_locked_reason) return true;
  if (!editable(state) || (!state.queue_mode && state.active)) return true;
  if (run.compatibility_warning && (action !== 'export' || run.suite === 'mcr')) return true;
  return run.suite === 'ccrab' && action === 'workflow' && !run.can_run;
}
export function caseLabel(status: string, live: boolean) {
  if (!live && ['reviewing', 'scoring'].includes(status)) return '已中断 · 需检查';
  if (!live && status === 'pending') return '未执行';
  return labels[status] ?? status;
}

export function resourceImportTask(task: ImportTask | null, repository: boolean) {
  const kind = repository ? 'repository' : 'dataset';
  return task?.kind === kind ? task : null;
}

function canResumeBatch(run: Run, job: Job | undefined, queueMode: boolean) {
  if (!queueMode || !['interrupted', 'cancelled'].includes(job?.status ?? '')) return false;
  if (run.compatibility_warning || run.reviewer !== 'lcr') return false;
  return !['mcr', 'ccrab'].includes(run.suite);
}

function partialReviewReady(run: Run, job?: Job) {
  return !run.compatibility_warning && run.status === 'partial' && run.completed > 0 && job?.status === 'succeeded' && job.action === 'review';
}
