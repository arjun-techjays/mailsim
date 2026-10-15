import { render, renderHook, screen } from '@testing-library/react';
import { App } from './App';
import { createApi } from './api/endpoints';
import { useApi, useSession } from './auth/session';
import { saveSession } from './auth/storage';
import { API_BASE } from './test/utils';

describe('App bootstrap', () => {
  it('renders the connect page in a browser router when no session exists', async () => {
    render(<App />);
    expect(await screen.findByRole('heading', { name: 'Connect to mailsim' })).toBeInTheDocument();
  });

  it('restores the session from sessionStorage', async () => {
    saveSession({ baseUrl: API_BASE, apiKey: 'mk_controller', role: 'controller' });
    window.history.pushState({}, '', '/runs');
    render(<App />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Runs' })).toBeInTheDocument();
    expect(screen.getByTestId('session-role')).toHaveTextContent('controller');
    window.history.pushState({}, '', '/');
  });
});

describe('session hooks', () => {
  it('throw outside the provider / without a session', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => renderHook(() => useSession())).toThrow('useSession must be used inside <SessionProvider>');
    expect(() => renderHook(() => useApi())).toThrow();
  });
});

describe('endpoints', () => {
  it('health is reachable without a key', async () => {
    await expect(createApi({ baseUrl: API_BASE, apiKey: '' }).health()).resolves.toEqual({ status: 'ok', version: 'mock' });
  });
});
