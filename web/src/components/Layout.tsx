import { useEffect, useRef, useState, type RefObject } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useSession } from '../auth/session';

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
  const operator = session && session.role !== 'agent';
  const reconnect = () => {
    disconnect();
    navigate('/connect');
  };
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header className="app-header">
        <span className="brand">mailsim</span>
        <nav aria-label="Main">
          <ul>
            {operator && (
              <>
                <li>
                  <NavLink to="/scenarios">Scenarios</NavLink>
                </li>
                <li>
                  <NavLink to="/runs">Runs</NavLink>
                </li>
              </>
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
            <span className="muted" data-testid="session-origin">
              API: {session.baseUrl}
              {session.baseUrl !== window.location.origin && ' (different origin from this dashboard)'}
            </span>
            <button type="button" onClick={reconnect}>
              Disconnect
            </button>
          </div>
        )}
      </header>
      {rejected && (
        <div role="alert" className="error-box banner" data-testid="key-rejected">
          <p className="error-title">The API rejected your key (401).</p>
          <p>It may have been revoked or expired.</p>
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
    </>
  );
}
