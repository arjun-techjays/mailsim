import { useMutation, useQuery } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import type { RunCreate, RunCreated } from '../api/types';
import { useApi } from '../auth/session';
import { rememberRun } from '../auth/storage';
import { CopyButton } from '../components/CopyButton';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { Loading } from '../components/Status';
import { usePageTitle } from '../hooks/usePageTitle';

/**
 * Agent credentials are returned exactly once by POST /scenarios/{key}/runs. They live only
 * in this component's state (the mutation cache entry is discarded immediately) and are gone
 * once the user dismisses them or leaves the page.
 */
function CredentialPanel({ created, onDismiss }: { created: RunCreated; onDismiss: () => void }) {
  return (
    <section aria-labelledby="cred-h" className="card warning" data-testid="agent-credentials">
      <h2 id="cred-h">Agent credential (shown once)</h2>
      <p role="alert">
        Copy this key now and give it only to the agent under test. It will not be shown again, and it is not stored
        by the dashboard.
      </p>
      {created.agent_credentials.map((c) => (
        <div key={c.api_key} className="credential">
          <dl className="kv">
            <dt>Mailbox</dt>
            <dd>
              {c.mailbox_address} (<code>{c.mailbox_id}</code>)
            </dd>
            <dt>Run</dt>
            <dd>
              <code>{c.run_id}</code>
            </dd>
          </dl>
          <label htmlFor={`key-${c.mailbox_id}`}>Agent API key for {c.mailbox_address}</label>
          <div className="row">
            <input
              id={`key-${c.mailbox_id}`}
              readOnly
              value={c.api_key}
              data-testid="agent-api-key"
              onFocus={(e) => e.currentTarget.select()}
              className="mono"
            />
            <CopyButton value={c.api_key} label="Copy key" />
          </div>
        </div>
      ))}
      <div className="row">
        <button type="button" onClick={onDismiss}>
          I have stored the key, hide it
        </button>
        <Link to={`/runs/${encodeURIComponent(created.run.id)}`}>Go to run {created.run.id}</Link>
      </div>
    </section>
  );
}

export function StartRunPage() {
  const { key = '' } = useParams();
  usePageTitle(`Start run · ${key}`);
  const api = useApi();
  const scenario = useQuery({ queryKey: ['scenario', key], queryFn: () => api.getScenario(key) });
  const [version, setVersion] = useState('');
  const [checks, setChecks] = useState<'' | 'on' | 'off'>('');
  const [build, setBuild] = useState('');
  const [start, setStart] = useState(true);
  const [created, setCreated] = useState<RunCreated | null>(null);
  const [dismissedRun, setDismissedRun] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (body: RunCreate) => api.createRun(key, body),
    gcTime: 0,
    onSuccess: (data) => {
      setCreated(data);
      rememberRun({ id: data.run.id, scenarioKey: key });
    },
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    setCreated(null);
    setDismissedRun(null);
    mutation.mutate({
      scenario_version: version ? Number(version) : null,
      checks: checks || null,
      agent_build: build.trim() || null,
      start,
    });
  };

  return (
    <>
      <p>
        <Link to={`/scenarios/${encodeURIComponent(key)}`}>← Scenario {key}</Link>
      </p>
      <h1>Start a run</h1>
      {scenario.isPending ? (
        <Loading />
      ) : scenario.isError ? (
        <ErrorDisplay error={scenario.error} title="Could not load scenario" />
      ) : (
        <form className="form" onSubmit={onSubmit}>
          <div className="field">
            <label htmlFor="sv">Scenario version</label>
            <select id="sv" value={version} onChange={(e) => setVersion(e.target.value)}>
              <option value="">Latest (v{scenario.data.latest_version?.version ?? '?'})</option>
              {scenario.data.versions.map((v) => (
                <option key={v} value={v}>
                  v{v}
                </option>
              ))}
            </select>
          </div>
          <fieldset>
            <legend>Post-run deterministic checks</legend>
            <p className="hint" id="checks-help">
              Evaluation preference only. It is stored with the run and never shown to the agent.
            </p>
            {(
              [
                ['', 'Answer-key default'],
                ['on', 'On'],
                ['off', 'Off'],
              ] as const
            ).map(([value, label]) => (
              <label key={value || 'default'} className="radio">
                <input
                  type="radio"
                  name="checks"
                  value={value}
                  checked={checks === value}
                  onChange={() => setChecks(value)}
                  aria-describedby="checks-help"
                />
                {label}
              </label>
            ))}
          </fieldset>
          <div className="field">
            <label htmlFor="build">Agent build id</label>
            <input id="build" value={build} maxLength={200} onChange={(e) => setBuild(e.target.value)} placeholder="e.g. agent-v2" />
          </div>
          <label className="checkbox">
            <input type="checkbox" checked={start} onChange={(e) => setStart(e.target.checked)} />
            Start the run immediately
          </label>
          <button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? 'Creating…' : 'Create run'}
          </button>
        </form>
      )}
      {mutation.isError && <ErrorDisplay error={mutation.error} title="Could not create run" />}
      {created && (
        <CredentialPanel
          created={created}
          onDismiss={() => {
            setDismissedRun(created.run.id);
            setCreated(null);
            mutation.reset(); // drop the credential from the mutation state as well
          }}
        />
      )}
      {dismissedRun && (
        <p role="status">
          Credential hidden. <Link to={`/runs/${encodeURIComponent(dismissedRun)}`}>Open run {dismissedRun}</Link>
        </p>
      )}
    </>
  );
}
