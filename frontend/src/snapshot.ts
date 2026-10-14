import type { Snapshot } from './types';

const invalid = () => new Error('工作台状态响应格式无效，请检查服务日志；页面会自动重试');
function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}
function rows(value: Record<string, unknown>, name: string): Record<string, unknown>[] {
  const entries = value[name];
  if (!Array.isArray(entries) || !entries.every(record)) throw invalid();
  return entries;
}
export function parseSnapshot(value: unknown): Snapshot {
  if (!record(value)) throw invalid();
  const catalog = rows(value, 'catalog'), runs = rows(value, 'runs'), comparisons = rows(value, 'comparisons');
  const profiles = rows(value, 'profiles'), jobs = rows(value, 'jobs');
  if (!catalog.every((suite) => typeof suite.id === 'string' && Array.isArray(suite.datasets))) throw invalid();
  if (!runs.every((run) => typeof run.key === 'string' && Array.isArray(run.cases) && Array.isArray(run.artifacts))) throw invalid();
  if (!comparisons.every((item) => record(item.report) && Array.isArray(item.report.groups))) throw invalid();
  if (!profiles.every((profile) => typeof profile.id === 'string' && typeof profile.label === 'string')) throw invalid();
  if (!jobs.every((job) => typeof job.id === 'string' && typeof job.status === 'string')) throw invalid();
  return value as unknown as Snapshot;
}
