import { screen, waitFor, within } from '@testing-library/react';
import { loadSession } from '../auth/storage';
import { MOCK_KEYS } from '../mocks/db';
import { API_BASE, renderApp, sessionFor } from '../test/utils';

describe('Connect page and session', () => {
  it('redirects to /connect when not connected and returns afterwards', async () => {
    const { user, router } = renderApp('/scenarios');
    expect(await screen.findByRole('heading', { name: 'Connect to mailsim' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/connect');
    expect(document.title).toBe('Connect · mailsim');

    await user.clear(screen.getByLabelText('API base URL'));
    await user.type(screen.getByLabelText('API base URL'), API_BASE);
    await user.type(screen.getByLabelText('API key'), MOCK_KEYS.controller);
    await user.click(screen.getByRole('button', { name: 'Connect' }));
    expect(await screen.findByTestId('detected-role')).toHaveTextContent('controller');
    expect(screen.getByLabelText('API key')).toHaveValue('');
    expect(loadSession()).toEqual({ baseUrl: API_BASE, apiKey: MOCK_KEYS.controller, role: 'controller' });
    expect(window.localStorage.length).toBe(0);
    expect(router.state.location.search).not.toContain(MOCK_KEYS.controller);

    await user.click(screen.getByRole('link', { name: 'Continue' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Scenarios' })).toBeInTheDocument();
  });

  it('validates the key and shows API errors', async () => {
    const { user } = renderApp('/connect');
    await user.clear(screen.getByLabelText('API base URL'));
    await user.type(screen.getByLabelText('API base URL'), API_BASE);
    await user.click(screen.getByRole('button', { name: 'Connect' }));
    expect(screen.getByText('Enter an API key.')).toBeInTheDocument();
    expect(screen.getByLabelText('API key')).toHaveAttribute('aria-invalid', 'true');

    await user.type(screen.getByLabelText('API key'), 'wrong');
    await user.click(screen.getByRole('button', { name: 'Connect' }));
    const alert = await screen.findByTestId('api-error');
    expect(alert).toHaveTextContent('Could not connect: UNAUTHENTICATED (HTTP 401)');
    expect(within(alert).getByTestId('request-id')).toHaveTextContent(/req_mock_/);
    expect(loadSession()).toBeNull();
  });

  it('defaults the base URL to the page origin', () => {
    renderApp('/connect');
    expect(screen.getByLabelText('API base URL')).toHaveValue(window.location.origin);
  });

  it('agent session: nav shows only the mailbox and home redirects there', async () => {
    const { user, router } = renderApp('/', sessionFor('agent'));
    expect(await screen.findByRole('heading', { name: 'My mailbox' })).toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).queryByRole('link', { name: 'Scenarios' })).toBeNull();
    expect(within(nav).getByRole('link', { name: 'My mailbox' })).toBeInTheDocument();

    await user.click(within(nav).getByRole('link', { name: 'Connect' }));
    expect(screen.getByTestId('detected-role')).toHaveTextContent('agent');
    expect(screen.getByRole('link', { name: 'Continue' })).toHaveAttribute('href', '/mailbox');

    await user.click(screen.getByRole('button', { name: 'Disconnect' }));
    expect(router.state.location.pathname).toBe('/connect');
    expect(screen.queryByTestId('session-info')).toBeNull();
    expect(loadSession()).toBeNull();
  });

  it('home redirects controllers to scenarios and anonymous users to connect', async () => {
    renderApp('/', sessionFor('controller'));
    expect(await screen.findByRole('heading', { level: 1, name: 'Scenarios' })).toBeInTheDocument();
  });

  it('home without a session goes to connect', async () => {
    const { router } = renderApp('/');
    await waitFor(() => expect(router.state.location.pathname).toBe('/connect'));
  });

  it('unknown paths show not found', () => {
    renderApp('/nope');
    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument();
  });
});
