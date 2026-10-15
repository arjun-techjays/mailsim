import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useSession } from '../auth/session';

export function Layout() {
  const { session, disconnect } = useSession();
  const navigate = useNavigate();
  const role = session?.role;
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header className="app-header">
        <span className="brand">mailsim</span>
        <nav aria-label="Main">
          <ul>
            {role && role !== 'agent' && (
              <>
                <li>
                  <NavLink to="/scenarios">Scenarios</NavLink>
                </li>
                <li>
                  <NavLink to="/runs">Runs</NavLink>
                </li>
              </>
            )}
            {role === 'agent' && (
              <li>
                <NavLink to="/mailbox">My mailbox</NavLink>
              </li>
            )}
            <li>
              <NavLink to="/connect">Connect</NavLink>
            </li>
          </ul>
        </nav>
        {session && (
          <div className="session" data-testid="session-info">
            <span>
              Role: <strong data-testid="session-role">{session.role}</strong>
            </span>
            <span className="muted">{session.baseUrl || 'same origin'}</span>
            <button
              type="button"
              onClick={() => {
                disconnect();
                navigate('/connect');
              }}
            >
              Disconnect
            </button>
          </div>
        )}
      </header>
      <main id="main" tabIndex={-1}>
        <Outlet />
      </main>
    </>
  );
}
