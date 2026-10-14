import { useCallback, useRef, useState } from 'react';
export function useAction() {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const pending = useRef(false);
  const generation = useRef(0);
  async function run(action: () => Promise<void>) {
    if (pending.current) return;
    const version = generation.current;
    pending.current = true;
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (error) {
      if (generation.current === version)
        setError(error instanceof Error ? error.message : String(error));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  const clear = useCallback(() => { generation.current++; setError(''); }, []);
  return { busy, error, run, clear };
}
