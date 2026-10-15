import { useState, type FormEvent } from 'react';
import { Link, useLocation } from 'react-router-dom';
import type { Role } from '../api/types';
import { detectRole } from '../auth/detectRole';
import { useSession } from '../auth/session';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { usePageTitle } from '../hooks/usePageTitle';

const CAPABILITIES: Record<Role, string> = {
  controller: 'Start, cancel, reset and replay runs; inject messages; control the virtual clock.',
  evaluator: 'Inspect state and traces; view answer keys (metadata), evaluate runs and compare builds.',
  agent: 'Read and modify the one simulated mailbox this key was issued for.',
};

function defaultBaseUrl(): string {
  return import.meta.env.VITE_DEFAULT_API_BASE || window.location.origin;
}

export function ConnectPage() {
  usePageTitle('Connect');
  const { session, connect } = useSession();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from;
  const [baseUrl, setBaseUrl] = useState(session?.baseUrl ?? defaultBaseUrl());
  const [apiKey, setApiKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [keyError, setKeyError] = useState('');

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!apiKey.trim()) {
      setKeyError('Enter an API key.');
      return;
    }
    setKeyError('');
    setBusy(true);
    try {
      const cfg = { baseUrl: baseUrl.trim(), apiKey: apiKey.trim() };
      const role = await detectRole(cfg);
      connect({ ...cfg, role });
      setApiKey('');
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const home = session?.role === 'agent' ? '/mailbox' : '/scenarios';
  return (
    <>
      <h1>Connect to mailsim</h1>
      <p>
        Enter the API base URL and a sandbox API key. The key is kept in this browser tab only (memory and
        sessionStorage) and is cleared when you disconnect or close the tab.
      </p>
      <form onSubmit={(e) => void onSubmit(e)} className="form" noValidate aria-describedby="connect-help">
        <div className="field">
          <label htmlFor="base-url">API base URL</label>
          <input id="base-url" type="url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} autoComplete="off" />
          <p id="connect-help" className="hint">
            Leave as this site’s origin to use the development proxy.
          </p>
        </div>
        <div className="field">
          <label htmlFor="api-key">API key</label>
          <input
            id="api-key"
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            aria-invalid={!!keyError}
            aria-describedby={keyError ? 'api-key-error' : undefined}
          />
          {keyError && (
            <p id="api-key-error" className="field-error">
              {keyError}
            </p>
          )}
        </div>
        <button type="submit" disabled={busy}>
          {busy ? 'Connecting…' : 'Connect'}
        </button>
      </form>
      {error != null && <ErrorDisplay error={error} title="Could not connect" />}
      {session && (
        <section aria-labelledby="connected-h" className="card" data-testid="connected">
          <h2 id="connected-h">Connected</h2>
          <p role="status">
            Detected role: <strong data-testid="detected-role">{session.role}</strong>
          </p>
          <p>{CAPABILITIES[session.role]}</p>
          <p>
            <Link to={from ?? home}>Continue</Link>
          </p>
        </section>
      )}
    </>
  );
}
