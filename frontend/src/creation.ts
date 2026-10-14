import { api, ApiError, AuthenticationRequired } from './api';
import { parseSnapshot } from './snapshot';
import type { JobReply } from './types';

export function creationName(prefix: string) {
  const suffix = crypto.getRandomValues(new Uint32Array(1))[0].toString(16).padStart(8, '0');
  return prefix + '-' + new Date().toISOString().replace(/\D/g, '') + '-' + suffix;
}

// Read back an accepted creation after a lost response; never replay a paid POST.
export async function submitCreation(path: string, data: unknown, key: string, action: string): Promise<JobReply> {
  try {
    return await api<JobReply>(path, data);
  } catch (error) {
    if (error instanceof AuthenticationRequired || (error instanceof ApiError && error.status < 500)) throw error;
    const accepted = await findCreation(key, action);
    if (accepted) return accepted;
    if (error instanceof ApiError) throw error;
    throw new Error('未能确认创建结果。请先到执行记录查看是否已有该编号的任务，避免重复创建。测评/实验编号：' + key);
  }
}
async function findCreation(key: string, action: string): Promise<JobReply | undefined> {
  try {
    const state = parseSnapshot(await api<unknown>('/api/state', undefined, 'GET', AbortSignal.timeout(5000)));
    const job = state.jobs.find((job) => job.key === key && job.action === action);
    return job ? { key, job_id: job.id } : undefined;
  } catch {
    return undefined;
  }
}
