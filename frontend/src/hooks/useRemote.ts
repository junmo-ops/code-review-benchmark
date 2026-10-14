import { useEffect, useState } from 'react';
import { api } from '../api';
export function useRemote<T>(path: string | undefined, interval = 0) {
  const [result, setResult] = useState<{ path?: string; data?: T; error?: string }>({});
  useEffect(() => {
    if (!path) return;
    let alive = true,
      pending = false;
    const controller = new AbortController();
    const load = async () => {
      if (pending) return;
      pending = true;
      try {
        const data = await api<T>(path, undefined, undefined, controller.signal);
        if (alive) setResult({ path, data });
      } catch (error) {
        if (alive)
          setResult({ path, error: error instanceof Error ? error.message : String(error) });
      } finally {
        pending = false;
      }
    };
    void load();
    const timer = interval ? setInterval(() => void load(), interval) : undefined;
    return () => {
      alive = false;
      controller.abort();
      clearInterval(timer);
    };
  }, [path, interval]);
  return result.path === path ? result : {};
}
