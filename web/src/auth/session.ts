import { createContext, useContext } from 'react';
import type { Api } from '../api/endpoints';
import type { Role } from '../api/types';
import type { Session } from './storage';

export interface SessionValue {
  session: Session | null;
  api: Api | null;
  connect: (session: Session) => void;
  disconnect: () => void;
  /** True once the API answered 401 for the current key (revoked/expired mid-session). */
  rejected: boolean;
  /** True while the dashboard checks whether the API runs in local no-auth mode. */
  probing: boolean;
  /** The API runs in local no-auth mode but this tab has no (valid) launch link. */
  localNoAuthLink: boolean;
}

export const SessionContext = createContext<SessionValue | null>(null);

export function useSession(): SessionValue {
  const v = useContext(SessionContext);
  if (!v) throw new Error('useSession must be used inside <SessionProvider>');
  return v;
}

/** The API client for the connected session. Only call under <RequireSession>. */
export function useApi(): Api {
  const { api } = useSession();
  if (!api) throw new Error('not connected');
  return api;
}

export function useRole(): Role | null {
  return useSession().session?.role ?? null;
}

/**
 * Whether `role` may use a view meant for `roles`. The local no-auth `operator` acts as a
 * controller AND an evaluator, never as an agent.
 */
export function hasRole(role: Role | null | undefined, roles: readonly Role[]): boolean {
  if (!role) return false;
  if (roles.includes(role)) return true;
  return role === 'operator' && roles.some((r) => r === 'controller' || r === 'evaluator');
}
