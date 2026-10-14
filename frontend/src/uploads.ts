import { api, ApiError } from './api';
import type { UploadSession } from './types';

export function readUploadSession(key: string): UploadSession | null {
  const stored = localStorage.getItem(key);
  if (!stored) return null;
  try {
    const value = JSON.parse(stored) as UploadSession;
    if (value && /^[a-f0-9]{32}$/.test(value.id) && typeof value.filename === 'string' &&
        Number.isSafeInteger(value.size) && value.size > 0 && /^[a-f0-9]{64}$/.test(value.fingerprint)) return value;
  } catch { /* Corrupt browser metadata must not block the server's upload state. */ }
  localStorage.removeItem(key);
  return null;
}
async function readServerSession(key: string): Promise<UploadSession | null> {
  const saved = readUploadSession(key);
  if (!saved) return null;
  try {
    const { upload } = await api<{ upload: UploadSession }>(`/api/uploads/${saved.id}`);
    if (['receiving', 'queued'].includes(upload.status)) return upload;
  } catch (error) {
    // A transient server/network failure must not discard a resumable upload.
    if (!(error instanceof ApiError && [400, 404].includes(error.status))) throw error;
  }
  localStorage.removeItem(key);
  return null;
}
export async function restoreUpload(kind: string, key: string) {
  const stored = await readServerSession(key);
  if (stored) return stored;
  const result = await api<{ upload: UploadSession | null }>(`/api/uploads/current?kind=${kind}`);
  if (result.upload) localStorage.setItem(key, JSON.stringify(result.upload));
  return result.upload;
}
