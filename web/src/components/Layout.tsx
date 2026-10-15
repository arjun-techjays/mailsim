import { useEffect, useRef, useState, type RefObject } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useSession } from '../auth/session';
import { AgentKeyProvider } from '../connect/AgentKeyProvider';
import { ThemeToggle } from '../theme/ThemeToggle';
import { StatusPill } from './StatusPill';

/** Moves focus to <main> and announces the new page title after client-side navigation. */
function useRouteAnnouncer(): [string, RefObject<HTMLElement | null>] {
  const { pathname } = useLocation();
  const main = useRef<HTMLElement>(null);
  const first = useRef(true);
  const [announcement, setAnnouncement] = useState('');
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    main.current?.focus();
    setAnnouncement(document.title);
  }, [pathname]);
  return [announcement, main];
}

export function Layout() {
  const { session, disconnect, rejected } = useSession();
  const navigate = useNavigate();
  const [announcement, main] = useRouteAnnouncer();
  const operator = !!session && session.role !== 'agent';
  const localNoAuth = session?.role === 'operator';
  const reconnect = () => {
    disconnect();
    navigate('/connect');
  };
  return (
    <AgentKeyProvider>
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header className="app-header">
        <div className="header-row">
          <Link to="/" className="brand">
            mailsim
          </Link>
          <nav aria-label="Main" className="primary-nav">
            <ul>
              {operator && (
                <>
                  <li>
                    <NavLink to="/" end>
                      Home
                    </NavLink>
                  </li>
                  <li>
                    <NavLink to="/scenarios">Scenarios</NavLink>
                  </li>
                  <li>
                    <NavLink to="/runs">Runs</NavLink>
                  </li>
                </>
              )}
              <li>
                <NavLink to="/concepts">Concepts</NavLink>
              </li>
              <li>
                <NavLink to="/connect">Connect</NavLink>
              </li>
            </ul>
          </nav>
          <div className="header-tools">
            {operator && <StatusPill />}
            <ThemeToggle />
          </div>
        </div>
        {session && (
          <div className="session" data-testid="session-info">
            <span>
              Role:{' '}
              <strong data-testid="session-role">
                {session.role === 'operator' ? 'operator (local no-auth)' : session.role}
              </strong>
            </span>
            <span className="muted" data-testid="session-origin">
              API: {session.baseUrl}
              {session.baseUrl !== window.location.origin && ' (different origin from this dashboard)'}
            </span>
            {!localNoAuth && (
              <button type="button" onClick={reconnect}>
                Disconnect
              </button>
            )}
          </div>
        )}
      </header>
      {localNoAuth && (
        <div role="note" className="notice banner" data-testid="local-no-auth-banner">
          <strong>Local no-auth mode.</strong> This dashboard needs no key and acts as controller and evaluator for
          the local project. Agent interfaces still require agent keys. Never expose this server beyond this machine.
        </div>
      )}
      {rejected && (
        <div role="alert" className="error-box banner" data-testid="key-rejected">
          {localNoAuth ? (
            <>
              <p className="error-title">The API rejected this tab’s local launch link (401).</p>
              <p>The server was probably restarted: open the new dashboard link it printed.</p>
            </>
          ) : (
            <>
              <p className="error-title">The API rejected your key (401).</p>
              <p>It may have been revoked or expired.</p>
            </>
          )}
          <button type="button" onClick={reconnect}>
            Reconnect
          </button>
        </div>
      )}
      <main id="main" tabIndex={-1} ref={main}>
        <Outlet />
      </main>
      <div className="visually-hidden" role="status" aria-live="polite" data-testid="route-announcer">
        {announcement}
      </div>
    </AgentKeyProvider>
  );
}
