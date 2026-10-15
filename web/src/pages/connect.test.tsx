import { screen, waitFor, within } from '@testing-library/react';
import { validateBaseUrl } from '../auth/baseUrl';
import { loadSession } from '../auth/storage';
import { MOCK_KEYS } from '../mocks/db';
import { API_BASE, renderApp, sessionFor } from '../test/utils';

async function fillAndConnect(user: ReturnType<typeof renderApp>['user'], base: string, key: string) {
  await user.clear(screen.getByLabelText('API base URL'));
  if (base) await user.type(screen.getByLabelText('API base URL'), base);
  if (key) await user.type(screen.getByLabelText('API key'), key);
  await user.click(screen.getByRole('button', { name: 'Connect' }));
}

describe('Connect page and session', () => {
  it('redirects to /connect when not connected and returns afterwards', async () => {
    const { user, router } = renderApp('/scenarios');
    expect(await screen.findByRole('heading', { name: 'Connect to mailsim' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/connect');
    expect(document.title).toBe('Connect · mailsim');

    await fillAndConnect(user, `${API_BASE}/`, MOCK_KEYS.controller);
    expect(await screen.findByTestId('detected-role')).toHaveTextContent('controller');
    expect(screen.getByLabelText('API key')).toHaveValue('');
    expect(loadSession()).toEqual({ baseUrl: API_BASE, apiKey: MOCK_KEYS.controller, role: 'controller' });
    expect(window.localStorage.length).toBe(0);
    expect(router.state.location.search).not.toContain(MOCK_KEYS.controller);
    expect(screen.getByTestId('connected-origin')).toHaveTextContent(`differs from this dashboard’s origin`);
    expect(screen.getByTestId('session-origin')).toHaveTextContent('different origin');

    await user.click(screen.getByRole('link', { name: 'Continue' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Scenarios' })).toBeInTheDocument();
    // route change: focus moves to <main> and the new title is announced
    await waitFor(() => expect(screen.getByTestId('route-announcer')).toHaveTextContent('Scenarios · mailsim'));
    expect(document.activeElement).toBe(document.getElementById('main'));
  });

  it('same-origin API is shown without a warning', async () => {
    const { user } = renderApp('/connect');
    await user.type(screen.getByLabelText('API key'), MOCK_KEYS.evaluator);
    await user.click(screen.getByRole('button', { name: 'Connect' }));
    expect(await screen.findByTestId('connected-origin')).not.toHaveTextContent('differs');
    expect(screen.getByTestId('session-origin')).not.toHaveTextContent('different origin');
  });

  it('validates key and base URL, and shows API errors', async () => {
    const { user } = renderApp('/connect');
    await fillAndConnect(user, 'https://user:pw@api.example.com', '');
    expect(screen.getByText('Enter an API key.')).toBeInTheDocument();
    expect(screen.getByText('The URL must not contain credentials.')).toBeInTheDocument();
    expect(screen.getByLabelText('API base URL')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByLabelText('API key')).toHaveAttribute('aria-invalid', 'true');

    await fillAndConnect(user, API_BASE, 'wrong');
    const alert = await screen.findByTestId('api-error');
    expect(alert).toHaveTextContent('Could not connect: UNAUTHENTICATED (HTTP 401)');
    expect(within(alert).getByTestId('request-id')).toHaveTextContent(/req_mock_/);
    expect(loadSession()).toBeNull();
  });

  it('defaults the base URL to the page origin', () => {
    renderApp('/connect');
    expect(screen.getByLabelText('API base URL')).toHaveValue(window.location.origin);
  });

  it('agent keys: explanatory page only, no operator navigation or data requests', async () => {
    const { user, router } = renderApp('/', sessionFor('agent'));
    expect(await screen.findByRole('heading', { name: 'This is an agent key' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/agent-key');
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).queryByRole('link', { name: 'Scenarios' })).toBeNull();
    expect(within(nav).queryByRole('link', { name: 'Runs' })).toBeNull();

    await user.click(within(nav).getByRole('link', { name: 'Connect' }));
    expect(screen.getByTestId('detected-role')).toHaveTextContent('agent');
    expect(screen.getByRole('link', { name: 'Continue' })).toHaveAttribute('href', '/agent-key');

    await user.click(screen.getByRole('button', { name: 'Disconnect' }));
    expect(router.state.location.pathname).toBe('/connect');
    expect(screen.queryByTestId('session-info')).toBeNull();
    expect(loadSession()).toBeNull();
  });

  it('home shows the getting-started checklist to controllers', async () => {
    renderApp('/', sessionFor('controller'));
    expect(await screen.findByRole('heading', { level: 1, name: 'Test your email agent' })).toBeInTheDocument();
    expect(screen.getByTestId('checklist')).toBeInTheDocument();
  });

  it('home without a session goes to connect', async () => {
    const { router } = renderApp('/');
    await waitFor(() => expect(router.state.location.pathname).toBe('/connect'));
  });

  it('unknown paths show not found', () => {
    renderApp('/nope');
    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument();
  });

  it('a 401 mid-session shows a reconnect prompt', async () => {
    const { user, router } = renderApp('/scenarios', { ...sessionFor('controller'), apiKey: 'revoked' });
    const banner = await screen.findByTestId('key-rejected');
    expect(banner).toHaveTextContent('The API rejected your key (401).');
    await user.click(within(banner).getByRole('button', { name: 'Reconnect' }));
    expect(router.state.location.pathname).toBe('/connect');
    expect(screen.queryByTestId('key-rejected')).toBeNull();
  });

  it('a 401 from a mutation also shows the prompt', async () => {
    const { user } = renderApp('/scenarios/finance.invoice-bank-change/start', sessionFor('controller'));
    await screen.findByRole('button', { name: 'Create run' });
    const { server } = await import('../test/server');
    const { http, HttpResponse } = await import('msw');
    server.use(
      http.post('*/v1/scenarios/:key/runs', () =>
        HttpResponse.json({ error: { code: 'UNAUTHENTICATED', message: 'revoked', details: {}, request_id: 'r' } }, { status: 401 }),
      ),
    );
    await user.click(screen.getByRole('button', { name: 'Create run' }));
    expect(await screen.findByTestId('key-rejected')).toBeInTheDocument();
  });
});

describe('validateBaseUrl', () => {
  it.each([
    ['https://api.example.com', true, 'https://api.example.com'],
    ['https://api.example.com/', true, 'https://api.example.com'],
    ['http://localhost:8000', true, 'http://localhost:8000'],
    ['http://127.0.0.1:8000', true, 'http://127.0.0.1:8000'],
    ['http://[::1]:8000', true, 'http://[::1]:8000'],
    ['http://api.example.com', false, 'Use https'],
    ['ftp://api.example.com', false, 'Only http and https'],
    ['javascript:alert(1)', false, 'Only http and https'],
    ['not a url', false, 'Enter a full URL'],
    ['https://u@api.example.com', false, 'credentials'],
    ['https://api.example.com/v1', false, 'Enter only the origin (https://api.example.com)'],
    ['https://api.example.com/?a=1', false, 'Enter only the origin'],
    ['https://api.example.com/#x', false, 'Enter only the origin'],
  ])('%s', (input, ok, expected) => {
    const r = validateBaseUrl(input);
    expect(r.ok).toBe(ok);
    if (r.ok) expect(r.origin).toBe(expected);
    else expect(r.error).toContain(expected);
  });
});
