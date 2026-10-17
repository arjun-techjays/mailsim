import { useMemo, useRef } from 'react';
import { isApiError, newIdempotencyKey } from '../api/client';

/**
 * One Idempotency-Key per user action, not per HTTP attempt: retrying the same request (same
 * body) reuses the key, so the server replays its first answer instead of creating a second run
 * or evaluation. Changing the request, or finishing the action (`done`), starts a new key.
 */
export function useActionKey() {
  const current = useRef<{ sig: string; key: string } | null>(null);
  return useMemo(
    () => ({
      keyFor(body: unknown): string {
        const sig = JSON.stringify(body);
        if (!current.current || current.current.sig !== sig) current.current = { sig, key: newIdempotencyKey() };
        return current.current.key;
      },
      done(): void {
        current.current = null;
      },
    }),
    [],
  );
}

/** The server is still working on an earlier attempt with the same Idempotency-Key. */
export function isInProgress(e: unknown): boolean {
  return isApiError(e) && e.status === 409 && 'idempotency_key' in e.details && /in progress/i.test(e.message);
}

/**
 * Failures after which the action may still have happened on the server (no answer, lost
 * connection, or still running): retrying with the same key is safe and tells which.
 */
export function isUnconfirmed(e: unknown): boolean {
  return isApiError(e) && (e.kind === 'timeout' || e.kind === 'network' || isInProgress(e));
}
