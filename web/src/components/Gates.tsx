import { useId, type ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import type { Role } from '../api/types';
import { useSession } from '../auth/session';
import type { Session } from '../auth/storage';

/** Operator pages: needs a session, and never runs with an agent key (no data requests). */
export function RequireSession({ children }: { children: ReactNode }) {
  const { session } = useSession();
  const location = useLocation();
  if (!session) return <Navigate to="/connect" replace state={{ from: location.pathname }} />;
  if (session.role === 'agent') return <Navigate to="/agent-key" replace />;
  return <>{children}</>;
}

type OperatorRole = Exclude<Role, 'agent'>;
const ARTICLE: Record<OperatorRole, string> = { controller: 'a', evaluator: 'an' };

/**
 * Client-side gate for role-specific views (always rendered under RequireSession). UX only:
 * the API enforces roles; evaluator-only data is never requested with another role.
 */
export function RequireRole({ roles, children, what }: { roles: OperatorRole[]; children: ReactNode; what: string }) {
  const { session } = useSession();
  const id = useId();
  const role = (session as Session).role as OperatorRole;
  if (roles.includes(role)) return <>{children}</>;
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
  return session && roles.includes(session.role) ? <>{children}</> : null;
}
