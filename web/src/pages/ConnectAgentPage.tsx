import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useApi } from '../auth/session';
import { CopyButton } from '../components/CopyButton';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { RequireRole } from '../components/Gates';
import { PageIntro } from '../components/PageIntro';
import { Badge, Loading } from '../components/Status';
import { WhatIsThis } from '../guide/Concepts';
import { connectionKey, useAgentKeys } from '../connect/agentKeys';
import { InterfaceDetails } from '../connect/InterfaceDetails';
import { KeyPanel } from '../connect/KeyPanel';
import { CHOICES, choiceById } from '../connect/snippets';
import { useSystemStatus } from '../guide/systemStatus';
import { usePageTitle } from '../hooks/usePageTitle';

function ConnectContent({ runId }: { runId: string }) {
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const [revealed, setRevealed] = useState(false);
  const held = useAgentKeys().get(runId);
  const conn = useQuery({ queryKey: connectionKey(runId), queryFn: () => api.runConnection(runId) });
  const status = useSystemStatus();
  // Local state answers the radio at once; the URL keeps the choice for reloads and links.
  const [choiceId, setChoiceId] = useState(params.get('interface'));
  const choice = choiceById(choiceId);

  if (conn.isPending) return <Loading label="Loading connection details…" />;
  if (conn.isError) return <ErrorDisplay error={conn.error} title="Could not load connection details" />;
  const c = conn.data;
  const pick = (id: string) => {
    setChoiceId(id);
    const p = new URLSearchParams(params);
    p.set('interface', id);
    setParams(p, { replace: true });
  };
  return (
    <>
      <section aria-labelledby="mbx-h" className="card" data-testid="connection-summary">
        <h2 id="mbx-h">The agent’s mailbox</h2>
        <dl className="kv">
          <dt>Address</dt>
          <dd className="row tight">
            <code data-testid="mailbox-address">{c.mailbox_address}</code>
            <CopyButton value={c.mailbox_address} label="Copy address" />
          </dd>
          <dt>Persona</dt>
          <dd>
            <code>{c.persona_id}</code> (mailbox <code>{c.mailbox_id}</code>)
          </dd>
          <dt>Run status</dt>
          <dd>
            <Badge value={c.run_status} />
          </dd>
        </dl>
        <WhatIsThis term="mailbox" />
        {c.run_status !== 'RUNNING' && (
          <p className="notice" role="note" data-testid="run-not-running">
            This run is {c.run_status}. Agents can connect only while it is RUNNING:{' '}
            <Link to={`/runs/${encodeURIComponent(runId)}`}>start it from the run page</Link>.
          </p>
        )}
      </section>

      <KeyPanel conn={c} revealed={revealed} onReveal={setRevealed} />

      <fieldset className="choices" data-testid="interface-chooser">
        <legend>How does your agent read and send mail?</legend>
        <p className="hint">Every interface reaches the same simulated mailbox. Pick the one your agent already speaks.</p>
        <div className="choice-grid">
          {CHOICES.map((ch) => (
            <label key={ch.id} className={`choice${choice?.id === ch.id ? ' selected' : ''}`}>
              <input type="radio" name="interface" value={ch.id} checked={choice?.id === ch.id} onChange={() => pick(ch.id)} />
              <span className="choice-title">
                {ch.title} <span className="muted">· {ch.alias}</span>
              </span>
              <span className="choice-when">Use this when {ch.useWhen}</span>
            </label>
          ))}
        </div>
      </fieldset>

      {choice ? (
        <InterfaceDetails choice={choice} conn={c} status={status.data} shownKey={revealed && held ? held.apiKey : null} />
      ) : (
        <p className="empty" data-testid="choose-interface">
          Choose an interface above to see its address, how authentication works, ready-to-paste code and a connection
          test.
        </p>
      )}
    </>
  );
}

export function ConnectAgentPage() {
  const { runId = '' } = useParams();
  usePageTitle(`Connect your agent · ${runId}`);
  const run = `/runs/${encodeURIComponent(runId)}`;
  return (
    <>
      <p>
        <Link to={run}>← Run {runId}</Link>
      </p>
      <h1>Connect your agent</h1>
      <PageIntro
        next={[
          { to: run, label: 'Back to the run: advance time and watch what the agent does' },
          { to: '/concepts', label: 'Concepts: mailbox, agent key, interfaces' },
        ]}
      >
        <p>
          Point the agent you are testing at this run’s simulated mailbox. It can use the Gmail API (GSuite simulator),
          Microsoft Graph (Office 365 simulator), IMAP/SMTP, MCP or plain REST: all of them reach the same mailbox, and
          nothing ever leaves the simulator. Give the agent only its agent key.
        </p>
      </PageIntro>
      <RequireRole roles={['controller']} what="Connecting an agent">
        <ConnectContent runId={runId} />
      </RequireRole>
    </>
  );
}
