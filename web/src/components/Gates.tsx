import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import type { Role } from '../api/types';
import { useSession } from '../auth/session';

export function RequireSession({ children }: { children: ReactNode }) {
  const { session } = useSession();
  const location = useLocation();
  if (!session) return <Navigate to="/connect" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

const ROLE_LABEL: Record<Role, string> = { controller: 'controller', evaluator: 'evaluator', agent: 'agent' };
const ARTICLE: Record<Role, string> = { controller: 'a', evaluator: 'an', agent: 'an' };

/**
 * Client-side gate for role-specific views. This is UX only: the API enforces roles and
 * returns 403 regardless; evaluator-only data is never requested with another role.
 */
export function RequireRole({ roles, children, what }: { roles: Role[]; children: ReactNode; what: string }) {
  const { session } = useSession();
  if (session && roles.includes(session.role)) return <>{children}</>;
  return (
    <div role="alert" className="notice" data-testid="role-gate">
      <p>
        <strong>{what}</strong> requires {roles.map((r) => `${ARTICLE[r]} ${ROLE_LABEL[r]}`).join(' or ')} key.
      </p>
      <p>
        You are connected as <strong>{session ? ROLE_LABEL[session.role] : 'nobody'}</strong>. Reconnect with a
        different key on the Connect page to use this view.
      </p>
    </div>
  );
}

/** Renders children only for the given roles (used to hide links and panels). */
export function ShowFor({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const { session } = useSession();
  return session && roles.includes(session.role) ? <>{children}</> : null;
}
