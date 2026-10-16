import { useMutation, useQuery } from '@tanstack/react-query';
import { useRef, useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import { isRedacted } from '../api/client';
import type { RunCreate, RunCreated, RunOut } from '../api/types';
import { useApi } from '../auth/session';
import { rememberRun } from '../auth/storage';
import { CopyButton } from '../components/CopyButton';
import { PageIntro } from '../components/PageIntro';
import { credentialKeyId, keyPrefix, useAgentKeys } from '../connect/agentKeys';
import { WhatIsThis } from '../guide/Concepts';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { Loading } from '../components/Status';
import { UnconfirmedNotice } from '../components/UnconfirmedNotice';
import { isUnconfirmed, useActionKey } from '../hooks/useActionKey';
import { usePageTitle } from '../hooks/usePageTitle';

/**
 * Agent credentials are returned exactly once by POST /scenarios/{key}/runs. They live only
 * in memory: this component's state and the tab's agent-key store (so "Connect your agent"
 * can use the key); never the URL, history state or browser storage. A reload forgets them.
 */
function CredentialPanel({ created, onDismiss }: { created: RunCreated; onDismiss: () => void }) {
  return (
    <section aria-labelledby="cred-h" className="card warning" data-testid="agent-credentials">
      <h2 id="cred-h">Agent credential (shown once)</h2>
      <p role="alert">
        Copy this key now and give it only to the agent under test. It will not be shown again. The dashboard keeps
        it in this tab’s memory only (for Connect your agent) and forgets it when you reload.
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
        <Link className="button primary" to={`/runs/${encodeURIComponent(created.run.id)}/connect`} data-testid="next-connect">
          Next: connect your agent
        </Link>
        <button type="button" onClick={onDismiss}>
          I have stored the key, hide it
        </button>
        <Link to={`/runs/${encodeURIComponent(created.run.id)}`}>Go to run {created.run.id}</Link>
      </div>
    </section>
  );
}

/**
 * A run recovered by retrying an unconfirmed create: the server replayed its response with the
 * agent key redacted (secrets are never stored for replays), so offer a fresh key instead.
 */
function RecoveredRun({ run, onMinted }: { run: RunOut; onMinted: (created: RunCreated) => void }) {
  const api = useApi();
  const mint = useMutation({
    mutationFn: () => api.mintAgentKey(run.id),
    gcTime: 0,
    onSuccess: (cred) => {
      onMinted({ run, agent_credentials: [cred] });
      mint.reset(); // the secret lives in the credential panel and key store only
    },
  });
  const runHref = `/runs/${encodeURIComponent(run.id)}`;
  return (
    <section aria-labelledby="recovered-h" className="card warning" data-testid="run-recovered">
      <h2 id="recovered-h">Run {run.id} was created</h2>
      <p role="status">
        Its agent key was in the response that never reached this tab, and the API shows a key only once. Mint a new
        agent key for this run; keys issued earlier keep working until you revoke them on its Connect page.
      </p>
      <div className="row">
        <button type="button" className="primary" disabled={mint.isPending} onClick={() => mint.mutate()}>
          {mint.isPending ? 'Minting…' : 'Mint a new agent key'}
        </button>
        <Link to={`${runHref}/connect`}>Connect your agent</Link>
        <Link to={runHref}>Go to run {run.id}</Link>
      </div>
      {mint.isError && <ErrorDisplay error={mint.error} title="Could not mint an agent key" />}
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
  const [recovered, setRecovered] = useState<RunOut | null>(null);
  const agentKeys = useAgentKeys();
  const action = useActionKey();
  const lastBody = useRef<RunCreate | null>(null);

  const show = (data: RunCreated) => {
    setCreated(data);
    const cred = data.agent_credentials[0];
    if (cred) {
      agentKeys.set(data.run.id, {
        apiKey: cred.api_key,
        prefix: keyPrefix(cred.api_key),
        mailboxAddress: cred.mailbox_address,
        mailboxId: cred.mailbox_id,
        keyId: credentialKeyId(cred),
      });
    }
  };

  const mutation = useMutation({
    mutationFn: (body: RunCreate) => api.createRun(key, body, { idempotencyKey: action.keyFor(body) }),
    gcTime: 0,
    onSuccess: (data) => {
      action.done();
      rememberRun({ id: data.run.id, scenarioKey: key });
      if (data.agent_credentials.some((c) => isRedacted(c.api_key))) setRecovered(data.run);
      else show(data);
    },
  });

  const submit = (body: RunCreate) => {
    lastBody.current = body;
    setCreated(null);
    setRecovered(null);
    setDismissedRun(null);
    mutation.mutate(body);
  };
  const [buildError, setBuildError] = useState('');
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (build.trim().toLowerCase() === '(no build)') {
      setBuildError('“(no build)” is how runs without a build id are labelled; leave the field empty for that.');
      return;
    }
    setBuildError('');
    submit({
      scenario_version: version ? Number(version) : null,
      checks: checks || null,
      agent_build: build.trim() || null,
      start,
    });
  };
  const unconfirmed = mutation.isError && isUnconfirmed(mutation.error);

  return (
    <>
      <p>
        <Link to={`/scenarios/${encodeURIComponent(key)}`}>← Scenario {key}</Link>
      </p>
      <h1>Start a run</h1>
      <PageIntro
        next={[
          { to: `/scenarios/${encodeURIComponent(key)}`, label: 'Back to the scenario' },
          { to: '/runs', label: 'Runs already started' },
        ]}
      >
        <p>
          A run is an isolated copy of this scenario: every persona gets a fresh mailbox and the virtual clock starts at
          the scenario’s start time. Creating it issues the agent key, shown once; next you connect your agent.
        </p>
        <WhatIsThis term="run" />
      </PageIntro>
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
            <WhatIsThis term="checks" />
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
            <input
              id="build"
              value={build}
              maxLength={200}
              onChange={(e) => setBuild(e.target.value)}
              placeholder="e.g. agent-v2"
              aria-invalid={!!buildError || undefined}
              aria-describedby={buildError ? 'build-err build-help' : 'build-help'}
            />
            <p id="build-help" className="hint">
              Compare builds groups runs by this id; runs without one are grouped as “(no build)”.
            </p>
            {buildError && (
              <p id="build-err" className="field-error">
                {buildError}
              </p>
            )}
          </div>
          <label className="checkbox">
            <input type="checkbox" checked={start} onChange={(e) => setStart(e.target.checked)} />
            Start the run immediately
          </label>
          <button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? 'Creating…' : 'Create run'}
          </button>
          {mutation.isPending && (
            <p className="hint" role="status">
              Creating the run and seeding its mailboxes; large scenarios can take a minute.
            </p>
          )}
        </form>
      )}
      {unconfirmed ? (
        <UnconfirmedNotice
          error={mutation.error}
          what="run"
          onRetry={() => submit(lastBody.current as RunCreate)}
        />
      ) : (
        mutation.isError && <ErrorDisplay error={mutation.error} title="Could not create run" />
      )}
      {recovered && (
        <RecoveredRun
          run={recovered}
          onMinted={(data) => {
            setRecovered(null);
            show(data);
          }}
        />
      )}
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
