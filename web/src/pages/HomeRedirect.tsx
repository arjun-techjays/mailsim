import { Navigate } from 'react-router-dom';
import { useSession } from '../auth/session';

export function HomeRedirect() {
  const { session } = useSession();
  if (!session) return <Navigate to="/connect" replace />;
  return <Navigate to={session.role === 'agent' ? '/agent-key' : '/scenarios'} replace />;
}
