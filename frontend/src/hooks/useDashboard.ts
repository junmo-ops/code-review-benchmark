import { useCallback, useEffect, useRef, useState } from 'react';
import { api, bootstrap } from '../api';
import type { ImportTask, Snapshot } from '../types';
import { parseSnapshot } from '../snapshot';
export function useDashboard() {
  const [state, setState] = useState<Snapshot>();
  const [task, setTask] = useState<ImportTask | null>(null);
  const [error, setError] = useState('');
  const stateRequest = useRef(false);
  const importRequest = useRef(false);
  const alive = useRef(true);
  const refresh = useCallback(async () => {
    if (stateRequest.current) return;
    stateRequest.current = true;
    try {
      await bootstrap();
      const incoming = parseSnapshot(await api<unknown>('/api/state?view=summary'));
      if (alive.current) {
        setState(incoming);
        setError('');
      }
    } catch (error) {
      if (alive.current) {
        setError(String(error instanceof Error ? error.message : error));
      }
    } finally {
      stateRequest.current = false;
    }
  }, []);
  const pollImport = useCallback(async () => {
    if (importRequest.current) return;
    importRequest.current = true;
    try {
      await bootstrap();
      const result = await api<{ task: ImportTask | null }>('/api/cases/imports');
      if (alive.current) setTask(result.task);
    } catch {
      /* State polling reports connection errors; retain the last import status. */
    } finally {
      importRequest.current = false;
    }
  }, []);
  useEffect(() => {
    alive.current = true;
    void refresh();
    void pollImport();
    const stateTimer = setInterval(() => void refresh(), 4000);
    const importTimer = setInterval(() => void pollImport(), 2000);
    return () => {
      alive.current = false;
      clearInterval(stateTimer);
      clearInterval(importTimer);
    };
  }, [refresh, pollImport]);
  return {
    state,
    task,
    setTask,
    error,
    refresh,
    importBusy: ['queued', 'running'].includes(task?.status ?? ''),
  };
}
