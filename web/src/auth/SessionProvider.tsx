import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { isApiError } from '../api/client';
import { createApi } from '../api/endpoints';
import { SessionContext, type SessionValue } from './session';
import { loadSession, saveSession, type Session } from './storage';

const is401 = (e: unknown) => isApiError(e) && e.status === 401;

export function SessionProvider({ children, initial }: { children: ReactNode; initial?: Session | null }) {
  const queryClient = useQueryClient();
  const [session, setSession] = useState<Session | null>(() => (initial !== undefined ? initial : loadSession()));
  const [rejected, setRejected] = useState(false);

  // Any 401 after connecting means the key was revoked or expired: prompt to reconnect.
  useEffect(() => {
    const unsubQ = queryClient.getQueryCache().subscribe((ev) => {
      if (ev.type === 'updated' && is401(ev.query.state.error)) setRejected(true);
    });
    const unsubM = queryClient.getMutationCache().subscribe((ev) => {
      if (ev.type === 'updated' && is401(ev.mutation.state.error)) setRejected(true);
    });
    return () => {
      unsubQ();
      unsubM();
    };
  }, [queryClient]);

  const connect = useCallback(
    (next: Session) => {
      queryClient.clear(); // never show data fetched with another key/role
      saveSession(next);
      setRejected(false);
      setSession(next);
    },
    [queryClient],
  );
  const disconnect = useCallback(() => {
    queryClient.clear();
    saveSession(null);
    setRejected(false);
    setSession(null);
  }, [queryClient]);

  const value = useMemo<SessionValue>(
    () => ({
      session,
      api: session ? createApi({ baseUrl: session.baseUrl, apiKey: session.apiKey }) : null,
      connect,
      disconnect,
      rejected,
    }),
    [session, connect, disconnect, rejected],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
