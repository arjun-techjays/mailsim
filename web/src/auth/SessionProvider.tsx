import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { isApiError } from '../api/client';
import { createApi } from '../api/endpoints';
import { defaultBaseUrl } from './baseUrl';
import { detectLocalNoAuth } from './detectRole';
import { SessionContext, type SessionValue } from './session';
import { loadSession, saveSession, type Session } from './storage';

const is401 = (e: unknown) => isApiError(e) && e.status === 401;

interface Props {
  children: ReactNode;
  initial?: Session | null;
  /** Check for local no-auth mode when there is no session. Default: only when `initial` is not given. */
  probe?: boolean;
  /** API origin probed for local no-auth mode (default: the dashboard's default API origin). */
  probeBaseUrl?: string;
}

export function SessionProvider({ children, initial, probe = initial === undefined, probeBaseUrl }: Props) {
  const queryClient = useQueryClient();
  const [session, setSession] = useState<Session | null>(() => (initial !== undefined ? initial : loadSession()));
  const [rejected, setRejected] = useState(false);
  const [probing, setProbing] = useState(() => probe && session === null);

  // Local no-auth mode: the API answers GET /v1/me without a key as the operator.
  useEffect(() => {
    if (!probing) return;
    let live = true;
    const baseUrl = probeBaseUrl ?? defaultBaseUrl();
    void detectLocalNoAuth(baseUrl).then((local) => {
      if (!live) return;
      if (local) setSession((cur) => cur ?? { baseUrl, apiKey: '', role: 'operator' });
      setProbing(false);
    });
    return () => {
      live = false;
    };
  }, [probing, probeBaseUrl]);

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
      setProbing(false);
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
      probing,
    }),
    [session, connect, disconnect, rejected, probing],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
