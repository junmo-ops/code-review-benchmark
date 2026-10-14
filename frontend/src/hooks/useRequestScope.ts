import { useEffect, useRef } from 'react';

// Responses may finish after a dialog closes, reopens, or changes its record.
export function useRequestScope(scope: unknown) {
  const session = useRef({ active: true });
  useEffect(() => {
    const current = { active: true };
    session.current = current;
    return () => { current.active = false; };
  }, [scope]);
  return () => {
    const current = session.current;
    return () => current.active;
  };
}
