import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { createApi } from '../api/endpoints';
import { SessionContext, type SessionValue } from './session';
import { loadSession, saveSession, type Session } from './storage';

export function SessionProvider({ children, initial }: { children: ReactNode; initial?: Session | null }) {
  const queryClient = useQueryClient();
  const [session, setSession] = useState<Session | null>(() => (initial !== undefined ? initial : loadSession()));

  const connect = useCallback(
    (next: Session) => {
      queryClient.clear(); // never show data fetched with another key/role
      saveSession(next);
      setSession(next);
    },
    [queryClient],
  );
  const disconnect = useCallback(() => {
    queryClient.clear();
    saveSession(null);
    setSession(null);
  }, [queryClient]);

  const value = useMemo<SessionValue>(
    () => ({
      session,
      api: session ? createApi({ baseUrl: session.baseUrl, apiKey: session.apiKey }) : null,
      connect,
      disconnect,
    }),
    [session, connect, disconnect],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
