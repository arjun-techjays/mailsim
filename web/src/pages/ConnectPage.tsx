import { useState, type FormEvent } from 'react';
import { Link, useLocation } from 'react-router-dom';
import type { Role } from '../api/types';
import { defaultBaseUrl, validateBaseUrl } from '../auth/baseUrl';
import { detectRole } from '../auth/detectRole';
import { useSession } from '../auth/session';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { PageIntro } from '../components/PageIntro';
import { usePageTitle } from '../hooks/usePageTitle';

const CAPABILITIES: Record<Role, string> = {
  controller: 'Start, cancel, reset and replay runs; inject messages; control the virtual clock.',
  evaluator: 'Inspect state and traces; view answer keys (metadata), evaluate runs and compare builds.',
  agent: 'Agent keys belong to the agent under test; the dashboard makes no requests with them.',
  operator:
    'Local no-auth mode: every controller and evaluator view, without a key. Agent interfaces still require agent keys.',
};

export function ConnectPage() {
  usePageTitle('Connect');
  const { session, connect, localNoAuthLink } = useSession();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from;
  const [baseUrl, setBaseUrl] = useState(session?.baseUrl ?? defaultBaseUrl());
  const [apiKey, setApiKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [keyError, setKeyError] = useState('');
  const [urlError, setUrlError] = useState('');

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    const url = validateBaseUrl(baseUrl);
    setUrlError(url.ok ? '' : url.error);
    setKeyError(apiKey.trim() ? '' : 'Enter an API key.');
    if (!url.ok || !apiKey.trim()) return;
    setBusy(true);
    try {
      const cfg = { baseUrl: url.origin, apiKey: apiKey.trim() };
      const role = await detectRole(cfg);
      connect({ ...cfg, role });
      setApiKey('');
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const home = session?.role === 'agent' ? '/agent-key' : '/scenarios';
  return (
    <>
      <h1>Connect to mailsim</h1>
      <PageIntro next={[{ to: '/concepts', label: 'Concepts: what the dashboard does' }]}>
        <p>
          Sign the dashboard in to a mailsim API server. Use a controller key to run scenarios and connect agents, or an
          evaluator key to grade runs. Bootstrap prints both (<code>uv run python -m mailsim.serve bootstrap</code>); in local
          no-auth mode, open the link the server printed instead. This is not where your agent connects: open a run and
          use <strong>Connect your agent</strong> for that.
        </p>
      </PageIntro>
      {localNoAuthLink && !session && (
        <div role="status" className="notice" data-testid="local-no-auth-link">
          <strong>This API runs in local no-auth mode.</strong> Open the dashboard link the server printed at startup
          (it carries a one-time local token, so no API key is needed), or connect with a key below.
        </div>
      )}
      <p>
        Enter the API base URL and a sandbox API key. The key is kept in this browser tab only (memory and
        sessionStorage) and is cleared when you disconnect or close the tab.
      </p>
      <form onSubmit={(e) => void onSubmit(e)} className="form" noValidate aria-label="Connect">
        <div className="field">
          <label htmlFor="base-url">API base URL</label>
          <input
            id="base-url"
            type="url"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            autoComplete="off"
            aria-invalid={!!urlError}
            aria-describedby={urlError ? 'connect-help base-url-error' : 'connect-help'}
          />
          <p id="connect-help" className="hint">
            Origin only (https, or http on localhost). Leave as this site’s origin to use the development proxy.
          </p>
          {urlError && (
            <p id="base-url-error" className="field-error">
              {urlError}
            </p>
          )}
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
          <p data-testid="connected-origin">
            API origin: <code>{session.baseUrl}</code>
            {session.baseUrl !== window.location.origin && (
              <strong> (differs from this dashboard’s origin {window.location.origin})</strong>
            )}
          </p>
          <p>
            <Link to={from ?? home}>Continue</Link>
          </p>
        </section>
      )}
    </>
  );
}
