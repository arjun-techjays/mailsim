import { QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { makeQueryClient } from '../queryClient';
import { mockDb, server } from '../test/server';
import { API_BASE, OPERATOR_SESSION, renderApp, sessionFor } from '../test/utils';
import { isLoopback } from './baseUrl';
import { detectLocalNoAuth } from './detectRole';
import { hasRole, useSession, type SessionValue } from './session';
import { SessionProvider } from './SessionProvider';
import { loadSession, saveSession, SESSION_STORAGE_KEY } from './storage';

const KEY = 'finance.invoice-bank-change';

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

describe('detectLocalNoAuth', () => {
  it('is true only when the loopback API answers /v1/me without a key as the operator', async () => {
    expect(await detectLocalNoAuth(API_BASE)).toBe(false); // 401: key mode
    mockDb.localNoAuth = true;
    expect(await detectLocalNoAuth(API_BASE)).toBe(true);
    expect(await detectLocalNoAuth('https://mailsim.example.com')).toBe(false); // never probed
    expect(isLoopback('not a url')).toBe(false);
  });

  it('ignores a key-less /v1/me that does not claim local mode, and network errors', async () => {
    const me = { role: 'controller', tenant_id: 't', project_id: 'p', run_id: null, mailbox_id: null, key_id: 'k', local_no_auth: false };
    server.use(http.get('*/v1/me', () => HttpResponse.json(me)));
    expect(await detectLocalNoAuth(API_BASE)).toBe(false);
    server.use(http.get('*/v1/me', () => HttpResponse.error()));
    expect(await detectLocalNoAuth(API_BASE)).toBe(false);
  });
});

describe('storage', () => {
  it('never persists the key-less operator session', () => {
    saveSession(sessionFor('controller'));
    expect(loadSession()?.role).toBe('controller');
    saveSession(OPERATOR_SESSION);
    expect(sessionStorage.getItem(SESSION_STORAGE_KEY)).toBeNull();
    sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(OPERATOR_SESSION));
    expect(loadSession()).toBeNull();
  });
});

describe('local no-auth mode in the dashboard', () => {
  it('skips the key screen and acts as controller + evaluator', async () => {
    mockDb.localNoAuth = true;
    const { router } = renderApp('/', null, { probe: true });
    expect(screen.getByText('Checking the API…')).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.pathname).toBe('/scenarios'));
    expect(screen.getByTestId('session-role')).toHaveTextContent('operator (local no-auth)');
    expect(screen.getByTestId('local-no-auth-banner')).toHaveTextContent('Agent interfaces still require agent keys');
    expect(screen.queryByRole('button', { name: 'Disconnect' })).not.toBeInTheDocument();

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

  it('sends no Authorization header in operator mode', async () => {
    mockDb.localNoAuth = true;
    const headers: (string | null)[] = [];
    server.events.on('request:start', ({ request }) => {
      headers.push(request.headers.get('authorization'));
    });
    renderApp('/scenarios', null, { probe: true });
    expect(await screen.findByTestId('session-role')).toHaveTextContent('operator');
    await waitFor(() => expect(headers.length).toBeGreaterThan(1));
    expect(headers.every((h) => h === null)).toBe(true);
  });

  it('shows the Connect page when the API is in key mode', async () => {
    const { router } = renderApp('/runs', null, { probe: true });
    await waitFor(() => expect(router.state.location.pathname).toBe('/connect'));
    expect(screen.queryByTestId('local-no-auth-banner')).not.toBeInTheDocument();
  });

  it('a key session wins over the probe, and connecting ends probing', async () => {
    mockDb.localNoAuth = true;
    renderApp('/scenarios', sessionFor('evaluator'), { probe: true });
    expect(await screen.findByTestId('session-role')).toHaveTextContent('evaluator');
    expect(screen.queryByTestId('local-no-auth-banner')).not.toBeInTheDocument();

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
    expect(ctx.current?.session?.role).toBe('controller'); // a late probe answer never replaces a key session
  });

  it('the mock mirrors the server: a bad key is still rejected in local mode', async () => {
    mockDb.localNoAuth = true;
    const bad = await fetch(`${API_BASE}/v1/scenarios`, { headers: { authorization: 'Bearer nope' } });
    expect(bad.status).toBe(401);
    const none = await fetch(`${API_BASE}/v1/scenarios`);
    expect(none.status).toBe(200);
  });
});
