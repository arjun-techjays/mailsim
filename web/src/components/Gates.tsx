import { useId, type ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import type { Role } from '../api/types';
import { hasRole, useSession } from '../auth/session';
import type { Session } from '../auth/storage';
import { Loading } from './Status';

/** Shown while the dashboard checks whether the API runs in local no-auth mode. */
export function Probing() {
  return <Loading label="Checking the API…" />;
}

/** Operator pages: needs a session, and never runs with an agent key (no data requests). */
export function RequireSession({ children }: { children: ReactNode }) {
  const { session, probing } = useSession();
  const location = useLocation();
  if (!session && probing) return <Probing />;
  if (!session) return <Navigate to="/connect" replace state={{ from: location.pathname }} />;
  if (session.role === 'agent') return <Navigate to="/agent-key" replace />;
  return <>{children}</>;
}

type ViewRole = Exclude<Role, 'agent' | 'operator'>;
const ARTICLE: Record<ViewRole, string> = { controller: 'a', evaluator: 'an' };

/**
 * Client-side gate for role-specific views (always rendered under RequireSession). UX only:
 * the API enforces roles; evaluator-only data is never requested with another role. The local
 * no-auth operator passes every controller/evaluator gate.
 */
export function RequireRole({ roles, children, what }: { roles: ViewRole[]; children: ReactNode; what: string }) {
  const { session } = useSession();
  const id = useId();
  const role = (session as Session).role;
  if (hasRole(role, roles)) return <>{children}</>;
  return (
    <section aria-labelledby={id} className="notice" data-testid="role-gate">
      <h2 id={id}>
        {what} requires {roles.map((r) => `${ARTICLE[r]} ${r}`).join(' or ')} key
      </h2>
      <p>
        You are connected as <strong>{role}</strong>. Reconnect with a different key on the Connect page to use this
        view.
      </p>
    </section>
  );
}

/** Renders children only for the given roles (used to hide links and panels). */
export function ShowFor({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const { session } = useSession();
  return hasRole(session?.role, roles) ? <>{children}</> : null;
}
