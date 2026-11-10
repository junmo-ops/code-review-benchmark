import { api, uploadChunk, uploadFingerprint } from '../../api';

export interface ZipTask {
  id: string;
  filename: string;
  output_file?: string;
  status: 'receiving' | 'queued' | 'running' | 'succeeded' | 'failed';
  message: string;
  offset: number;
  size: number;
  expires_at: number;
  commits?: number;
  branches?: number;
}
export interface ZipListing {
  tasks: ZipTask[];
  limits: { max_bytes: number; chunk_size: number; retention_hours: number };
}

export async function sendZip(file: File, main: string, progress: (percent: number) => void) {
  const fingerprint = await uploadFingerprint(file);
  const { task } = await api<{ task: ZipTask }>('/api/zip-tool', {
    filename: file.name, size: file.size, fingerprint, main: main.trim(),
  });
  const chunkSize = 8 * 1024 * 1024;
  for (let offset = task.offset; offset < file.size; offset += chunkSize) {
    await uploadChunk(task.id, offset, file.slice(offset, offset + chunkSize),
      (sent) => progress(Math.floor(((offset + sent) / file.size) * 100)),
      `/api/zip-tool/${task.id}/chunks/${offset}`);
  }
  progress(100);
  await api(`/api/zip-tool/${task.id}/complete`, {});
}

export async function downloadZipResult(task: ZipTask, kind: string) {
  const { url } = await api<{ url: string }>(`/api/zip-tool/${task.id}/download-ticket`, { kind });
  // Let the browser stream to disk, instead of buffering multi-GB ZIPs in a Blob.
  const link = document.createElement('a');
  link.href = url;
  link.download = '';
  link.click();
}
