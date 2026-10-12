import { useCallback, useEffect, useRef, useState } from 'react';
import { api, AuthenticationRequired, bootstrap, setAuthentication } from '../api';
import type { ImportTask, Snapshot } from '../types';
export function useDashboard() {
  const [state, setState] = useState<Snapshot>();
  const [task, setTask] = useState<ImportTask | null>(null);
  const [error, setError] = useState('');
  const [loginRequired, setLoginRequired] = useState(false);
  const stateRequest = useRef(false);
  const importRequest = useRef(false);
  const alive = useRef(true);
  const refresh = useCallback(async () => {
    if (stateRequest.current) return;
    stateRequest.current = true;
    try {
      await bootstrap();
      const incoming = await api<Snapshot>('/api/state');
      if (alive.current) {
        setState(incoming);
        setError('');
        setLoginRequired(false);
      }
    } catch (error) {
      if (alive.current) {
        setError(String(error instanceof Error ? error.message : error));
        if (error instanceof AuthenticationRequired) {
          setLoginRequired(true);
          setState(undefined);
        }
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
    loginRequired,
    login: async (username: string, password: string) => {
      setAuthentication(username, password);
      await refresh();
    },
    state,
    task,
    setTask,
    error,
    refresh,
    importBusy: ['queued', 'running'].includes(task?.status ?? ''),
  };
}
