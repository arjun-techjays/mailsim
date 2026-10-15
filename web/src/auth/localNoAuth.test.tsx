import { QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { MOCK_LOCAL_TOKEN } from '../mocks/db';
import { makeQueryClient } from '../queryClient';
import { mockDb, server } from '../test/server';
import { API_BASE, OPERATOR_SESSION, renderApp, sessionFor } from '../test/utils';
import { isLoopback } from './baseUrl';
import { detectLocalNoAuth } from './detectRole';
import { resetLaunchToken, takeLaunchToken } from './launchToken';
import { hasRole, useSession, type SessionValue } from './session';
import { SessionProvider } from './SessionProvider';
import { loadSession, saveSession, SESSION_STORAGE_KEY } from './storage';

const KEY = 'finance.invoice-bank-change';

function openLaunchLink(token = MOCK_LOCAL_TOKEN, path = '/') {
  window.history.replaceState(null, '', `${path}#local_token=${token}`);
}

describe('hasRole', () => {
  it('lets the local operator act as controller and evaluator, never as agent', () => {
    expect(hasRole('operator', ['controller'])).toBe(true);
    expect(hasRole('operator', ['evaluator'])).toBe(true);
    expect(hasRole('operator', ['agent'])).toBe(false);
    expect(hasRole('controller', ['evaluator'])).toBe(false);
    expect(hasRole('evaluator', ['evaluator'])).toBe(true);
    expect(hasRole(null, ['controller'])).toBe(false);
  });
});

describe('launch token', () => {
  it('is read once from the URL fragment and removed from the address bar', () => {
    openLaunchLink(MOCK_LOCAL_TOKEN, '/runs');
    expect(takeLaunchToken()).toBe(MOCK_LOCAL_TOKEN);
    expect(window.location.hash).toBe('');
    expect(window.location.pathname).toBe('/runs');
    expect(takeLaunchToken()).toBe(MOCK_LOCAL_TOKEN); // cached (StrictMode double init)
    resetLaunchToken();
    expect(takeLaunchToken()).toBeNull();
    resetLaunchToken();
    window.history.replaceState(null, '', '/#local_token=short');
    expect(takeLaunchToken()).toBeNull();
  });
});

describe('detectLocalNoAuth', () => {
  it('distinguishes operator, needs-link and key mode', async () => {
    expect(await detectLocalNoAuth(API_BASE)).toBe('off'); // key mode: plain 401
    mockDb.localNoAuth = true;
    expect(await detectLocalNoAuth(API_BASE)).toBe('needs-link');
    expect(await detectLocalNoAuth(API_BASE, 'wrong-token-xxxxxxxx')).toBe('needs-link');
    expect(await detectLocalNoAuth(API_BASE, MOCK_LOCAL_TOKEN)).toBe('operator');
    expect(await detectLocalNoAuth('https://mailsim.example.com', MOCK_LOCAL_TOKEN)).toBe('off'); // never probed
    expect(isLoopback('not a url')).toBe(false);
  });

  it('ignores answers that do not claim local mode, and network errors', async () => {
    const me = { role: 'controller', tenant_id: 't', project_id: 'p', run_id: null, mailbox_id: null, key_id: 'k', local_no_auth: false };
    server.use(http.get('*/v1/me', () => HttpResponse.json(me)));
    expect(await detectLocalNoAuth(API_BASE, MOCK_LOCAL_TOKEN)).toBe('off');
    server.use(http.get('*/v1/me', () => HttpResponse.error()));
    expect(await detectLocalNoAuth(API_BASE)).toBe('off');
  });
});

describe('storage', () => {
  it('keeps the operator session (with its token) in this tab only, like keys', () => {
    saveSession(OPERATOR_SESSION);
    expect(loadSession()).toEqual(OPERATOR_SESSION);
    sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify({ ...OPERATOR_SESSION, localToken: '' }));
    expect(loadSession()).toBeNull();
    saveSession(null);
    expect(sessionStorage.getItem(SESSION_STORAGE_KEY)).toBeNull();
  });
});

describe('local no-auth mode in the dashboard', () => {
  it('a launch link skips the key screen and acts as controller + evaluator', async () => {
    mockDb.localNoAuth = true;
    openLaunchLink();
    const { router } = renderApp('/', null, { probe: true });
    expect(screen.getByText('Checking the API…')).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.pathname).toBe('/scenarios'));
    expect(screen.getByTestId('session-role')).toHaveTextContent('operator (local no-auth)');
    expect(screen.getByTestId('local-no-auth-banner')).toHaveTextContent('Agent interfaces still require agent keys');
    expect(screen.queryByRole('button', { name: 'Disconnect' })).not.toBeInTheDocument();
    expect(loadSession()?.localToken).toBe(MOCK_LOCAL_TOKEN);

    await act(() => router.navigate(`/scenarios/${KEY}`));
    expect(await screen.findByRole('link', { name: 'Start a run' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Compare builds' })).toBeInTheDocument();
    expect(await screen.findByTestId('answer-keys')).toBeInTheDocument();

    await act(() => router.navigate('/runs/run_demo'));
    expect(await screen.findByTestId('run-summary')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Evaluations' })).toBeInTheDocument();
    expect(screen.queryByText(/require a controller key/)).not.toBeInTheDocument();

    await act(() => router.navigate(`/scenarios/${KEY}/compare`));
    expect(await screen.findByTestId('comparison-table')).toBeInTheDocument();
    expect(screen.queryByTestId('role-gate')).not.toBeInTheDocument();
  });

  it('sends the launch token, never an Authorization header', async () => {
    mockDb.localNoAuth = true;
    openLaunchLink();
    const seen: [string | null, string | null][] = [];
    server.events.on('request:start', ({ request }) => {
      seen.push([request.headers.get('authorization'), request.headers.get('x-mailsim-local-token')]);
    });
    renderApp('/scenarios', null, { probe: true });
    expect(await screen.findByTestId('session-role')).toHaveTextContent('operator');
    await waitFor(() => expect(seen.length).toBeGreaterThan(1));
    expect(seen.every(([auth, token]) => auth === null && token === MOCK_LOCAL_TOKEN)).toBe(true);
  });

  it('without the link, the Connect page explains where to find it', async () => {
    mockDb.localNoAuth = true;
    const { router } = renderApp('/runs', null, { probe: true });
    await waitFor(() => expect(router.state.location.pathname).toBe('/connect'));
    expect(await screen.findByTestId('local-no-auth-link')).toHaveTextContent('Open the dashboard link');
    expect(screen.queryByTestId('local-no-auth-banner')).not.toBeInTheDocument();
  });

  it('shows the plain Connect page when the API is in key mode', async () => {
    const { router } = renderApp('/runs', null, { probe: true });
    await waitFor(() => expect(router.state.location.pathname).toBe('/connect'));
    expect(screen.queryByTestId('local-no-auth-link')).not.toBeInTheDocument();
  });

  it('a stale token (server restarted) asks for the new link', async () => {
    mockDb.localNoAuth = true;
    renderApp('/scenarios', { ...OPERATOR_SESSION, localToken: 'stale-token-xxxxxxxxxx' });
    expect(await screen.findByTestId('key-rejected')).toHaveTextContent('open the new dashboard link');
  });

  it('a key session without a link is kept; an explicit connect wins over a late probe', async () => {
    mockDb.localNoAuth = true;
    renderApp('/scenarios', sessionFor('evaluator'), { probe: true });
    expect(await screen.findByTestId('session-role')).toHaveTextContent('evaluator');
    expect(screen.queryByTestId('local-no-auth-banner')).not.toBeInTheDocument();

    openLaunchLink();
    const ctx: { current: SessionValue | null } = { current: null };
    function Grab() {
      ctx.current = useSession();
      return null;
    }
    render(
      <QueryClientProvider client={makeQueryClient()}>
        <SessionProvider initial={null} probe probeBaseUrl={API_BASE}>
          <Grab />
        </SessionProvider>
      </QueryClientProvider>,
    );
    expect(ctx.current?.probing).toBe(true);
    act(() => ctx.current?.connect(sessionFor('controller')));
    expect(ctx.current?.probing).toBe(false);
    await new Promise((r) => setTimeout(r, 50));
    expect(ctx.current?.session?.role).toBe('controller');
  });

  it('the mock mirrors the server: a bad key is still rejected in local mode', async () => {
    mockDb.localNoAuth = true;
    const bad = await fetch(`${API_BASE}/v1/scenarios`, { headers: { authorization: 'Bearer nope' } });
    expect(bad.status).toBe(401);
    expect(bad.headers.get('x-mailsim-local-no-auth')).toBeNull();
    const ok = await fetch(`${API_BASE}/v1/scenarios`, { headers: { 'x-mailsim-local-token': MOCK_LOCAL_TOKEN } });
    expect(ok.status).toBe(200);
  });
});
