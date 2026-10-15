import { Navigate } from 'react-router-dom';
import { useSession } from '../auth/session';
import { Probing } from '../components/Gates';

export function HomeRedirect() {
  const { session, probing } = useSession();
  if (!session && probing) return <Probing />;
  if (!session) return <Navigate to="/connect" replace />;
  return <Navigate to={session.role === 'agent' ? '/agent-key' : '/scenarios'} replace />;
}
