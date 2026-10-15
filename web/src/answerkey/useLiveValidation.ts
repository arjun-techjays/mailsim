import { useEffect, useState } from 'react';
import type { AnswerKeyValidation } from '../api/types';
import { useApi } from '../auth/session';

export const VALIDATE_DEBOUNCE_MS = 500;

export interface LiveValidation {
  pending: boolean;
  result: AnswerKeyValidation | null;
  error: unknown;
}

/**
 * Validates the draft on the server (schema + leak guard; stores nothing) shortly after the
 * user stops typing. Stale responses are aborted; the last result stays visible meanwhile.
 */
export function useLiveValidation(
  scenarioKey: string,
  body: { document?: Record<string, unknown>; source?: string },
): LiveValidation {
  const api = useApi();
  const [state, setState] = useState<LiveValidation>({ pending: true, result: null, error: null });
  const payload = JSON.stringify(body);

  useEffect(() => {
    const controller = new AbortController();
    setState((s) => ({ ...s, pending: true }));
    const timer = setTimeout(() => {
      api
        .validateAnswerKey(scenarioKey, JSON.parse(payload) as typeof body, controller.signal)
        .then((result) => setState({ pending: false, result, error: null }))
        .catch((error: unknown) => {
          if (!controller.signal.aborted) setState({ pending: false, result: null, error });
        });
    }, VALIDATE_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
    // `payload` is the serialized body: re-run only when the content changes.
  }, [api, scenarioKey, payload]);

  return state;
}
