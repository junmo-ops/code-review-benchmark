import { useCallback, useEffect, useRef, useState } from 'react';
import type { ImportTask } from '../types';
export interface BatchItem {
  name: string;
  size: number;
  status: 'waiting' | 'uploading' | 'importing' | 'succeeded' | 'failed';
  taskId?: string;
}
function readBatch(key: string): BatchItem[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key) || '[]');
    if (!Array.isArray(value)) return [];
    return value.filter((item): item is BatchItem =>
      typeof item?.name === 'string' && typeof item?.size === 'number' &&
      ['waiting', 'uploading', 'importing', 'succeeded', 'failed'].includes(item?.status));
  } catch { return []; }
}
export function useUploadBatch(kind: string, task: ImportTask | null) {
  const key = `bench.upload.batch.v1.${kind}`;
  const [items, setItems] = useState(() => readBatch(key));
  const current = useRef(items);
  const save = useCallback((next: BatchItem[]) => {
    current.current = next;
    if (next.length) localStorage.setItem(key, JSON.stringify(next));
    else localStorage.removeItem(key);
    setItems(next);
  }, [key]);
  const update = useCallback((file: Pick<File, 'name' | 'size'>, change: Partial<BatchItem>) => {
    save(current.current.map((item) => item.name === file.name && item.size === file.size
      ? { ...item, ...change } : item));
  }, [save]);
  useEffect(() => {
    if (!task || ['queued', 'running'].includes(task.status)) return;
    const item = current.current.find((entry) => entry.taskId === task.id && entry.status === 'importing');
    if (item) update(item, { status: task.status === 'succeeded' ? 'succeeded' : 'failed' });
  }, [task, update]);
  const prepare = (selected: File[]) => {
    const active = current.current.filter((item) => item.status !== 'succeeded');
    if (!active.length) {
      const names = selected.map((file) => `${file.name}:${file.size}`);
      if (new Set(names).size !== names.length) throw new Error('请勿重复选择同一个 ZIP');
      save(selected.map((file) => ({ name: file.name, size: file.size, status: 'waiting' })));
      return selected;
    }
    for (const file of selected) {
      if (!active.some((item) => item.name === file.name && item.size === file.size))
        throw new Error('当前仍有未完成的批量清单，请选择清单中的剩余 ZIP，或先结束本批次');
    }
    return active.flatMap((item) => selected.filter((file) => file.name === item.name && file.size === item.size));
  };
  return { items, prepare, update, clear: () => save([]) };
}
