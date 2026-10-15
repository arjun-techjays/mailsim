import { useEffect, useState } from 'react';

/** `value`, updated only after it has been stable for `ms` milliseconds. */
export function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return settled;
}

/** Debounce for live validation and the YAML view (ms). */
export const LIVE_DEBOUNCE_MS = 400;
