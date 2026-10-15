import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { isApiError } from '../api/client';
import { createApi } from '../api/endpoints';
import { defaultBaseUrl } from './baseUrl';
import { detectLocalNoAuth } from './detectRole';
import { takeLaunchToken } from './launchToken';
import { SessionContext, type SessionValue } from './session';
import { loadSession, saveSession, type Session } from './storage';
import { clearAllDrafts } from '../answerkey/draftStorage';

const is401 = (e: unknown) => isApiError(e) && e.status === 401;

interface Props {
  children: ReactNode;
  initial?: Session | null;
  /** Check for local no-auth mode (launch link / no session). Default: only when `initial` is not given. */
  probe?: boolean;
  /** API origin probed for local no-auth mode (default: the dashboard's default API origin). */
  probeBaseUrl?: string;
}

export function SessionProvider({ children, initial, probe = initial === undefined, probeBaseUrl }: Props) {
  const queryClient = useQueryClient();
  const [session, setSession] = useState<Session | null>(() => (initial !== undefined ? initial : loadSession()));
  const [rejected, setRejected] = useState(false);
  const [launchToken] = useState(() => (probe ? takeLaunchToken() : null));
  const [probing, setProbing] = useState(() => probe && (session === null || launchToken !== null));
  const [localNoAuthLink, setLocalNoAuthLink] = useState(false);
  const chosen = useRef(false); // the user connected explicitly: a late probe never overrides that

  // Local no-auth mode: a launch link (token in the URL fragment) makes this tab the operator.
  useEffect(() => {
    if (!probing) return;
    let live = true;
    const baseUrl = probeBaseUrl ?? defaultBaseUrl();
    void detectLocalNoAuth(baseUrl, launchToken ?? undefined).then((mode) => {
      if (!live || chosen.current) return;
      if (mode === 'operator' && launchToken) {
        const operator: Session = { baseUrl, apiKey: '', role: 'operator', localToken: launchToken };
        queryClient.clear();
        clearAllDrafts();
        saveSession(operator);
        setSession(operator);
      }
      setLocalNoAuthLink(mode === 'needs-link');
      setProbing(false);
    });
    return () => {
      live = false;
    };
  }, [probing, probeBaseUrl, launchToken, queryClient]);

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
      clearAllDrafts(); // answer-key drafts never outlive the session that wrote them
      saveSession(next);
      setRejected(false);
      setProbing(false);
      chosen.current = true;
      setSession(next);
    },
    [queryClient],
  );
  const disconnect = useCallback(() => {
    queryClient.clear();
    clearAllDrafts();
    saveSession(null);
    setRejected(false);
    setSession(null);
  }, [queryClient]);

  const value = useMemo<SessionValue>(
    () => ({
      session,
      api: session
        ? createApi({ baseUrl: session.baseUrl, apiKey: session.apiKey, localToken: session.localToken })
        : null,
      connect,
      disconnect,
      rejected,
      probing,
      localNoAuthLink,
    }),
    [session, connect, disconnect, rejected, probing, localNoAuthLink],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
