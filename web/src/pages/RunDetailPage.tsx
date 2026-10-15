import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { hasRole, useApi, useRole } from '../auth/session';
import { rememberRun } from '../auth/storage';
import { ShowFor } from '../components/Gates';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { Badge, Loading } from '../components/Status';
import { usePageTitle } from '../hooks/usePageTitle';
import { parseSnapshot } from '../mailbox/snapshot';
import { ActionsTable } from '../run/ActionsTable';
import { ArtifactsTable } from '../run/ArtifactsTable';
import { EventTimeline } from '../run/EventTimeline';
import { InjectForm } from '../run/InjectForm';
import { runKeys } from '../run/keys';
import { RunControls } from '../run/RunControls';
// ---- mailbox browser, activity & toolbar (UX-F2)
import { MailboxBrowser } from '../mailbox/MailboxBrowser';
import { ActivityPanel } from '../run/ActivityPanel';
import { RunToolbar } from '../run/RunToolbar';

const TABS = [
  ['overview', 'Overview'],
  ['mailboxes', 'Mailboxes'],
  ['activity', 'Activity'],
  ['trace', 'Event trace'],
  ['actions', 'Actions'],
  ['artifacts', 'Artifacts'],
] as const;
type Tab = (typeof TABS)[number][0];

export function RunDetailPage() {
  const { runId = '' } = useParams();
  usePageTitle(`Run ${runId}`);
  const api = useApi();
  const role = useRole();
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find(([t]) => t === params.get('tab'))?.[0] ?? 'overview') as Tab;

  const run = useQuery({ queryKey: runKeys.all(runId), queryFn: () => api.getRun(runId) });
  const needsState = run.isSuccess && (tab === 'mailboxes' || (tab === 'overview' && hasRole(role, ['controller'])));
  const state = useQuery({ queryKey: runKeys.state(runId), queryFn: () => api.state(runId), enabled: needsState });
  const snapshot = useMemo(() => (state.data ? parseSnapshot(state.data) : undefined), [state.data]);

  const scenarioKey = run.data?.scenario_key;
  useEffect(() => {
    if (scenarioKey !== undefined) rememberRun({ id: runId, scenarioKey });
  }, [runId, scenarioKey]);

  const go = (next: Record<string, string | null | undefined>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) {
      if (v == null || v === '') p.delete(k);
      else p.set(k, v);
    }
    setParams(p);
  };
  const tabHref = (t: Tab) => `?tab=${t}`;
  const seq = params.get('seq');

  return (
    <>
      <p>
        <Link to="/runs">← Runs</Link>
      </p>
      <h1>
        Run <code>{runId}</code>
      </h1>
      {run.isPending ? (
        <Loading label="Loading run…" />
      ) : run.isError ? (
        <ErrorDisplay error={run.error} title="Could not load run" />
      ) : (
        <>
          <dl className="kv summary" data-testid="run-summary">
            <dt>Status</dt>
            <dd>
              <Badge value={run.data.status} />
            </dd>
            <dt>Epoch</dt>
            <dd data-testid="run-epoch">{run.data.epoch}</dd>
            <dt>Virtual time</dt>
            <dd data-testid="run-virtual-time">{run.data.virtual_time ?? '—'}</dd>
            <dt>Agent actions</dt>
            <dd>{run.data.agent_actions}</dd>
            <dt>Scenario</dt>
            <dd>
              {run.data.scenario_key ? (
                <Link to={`/scenarios/${encodeURIComponent(run.data.scenario_key)}`}>
                  {run.data.scenario_key} v{run.data.scenario_version}
                </Link>
              ) : (
                '—'
              )}
            </dd>
            <dt>Agent build</dt>
            <dd>{run.data.agent_build ?? '—'}</dd>
            {run.data.replay_of && (
              <>
                <dt>Replay of</dt>
                <dd>
                  <Link to={`/runs/${encodeURIComponent(run.data.replay_of)}`}>{run.data.replay_of}</Link>
                </dd>
              </>
            )}
          </dl>
          <div className="row">
            <button type="button" onClick={() => void run.refetch()}>
              Refresh
            </button>
            <ShowFor roles={['evaluator']}>
              <Link className="button" to={`/runs/${encodeURIComponent(runId)}/evaluations`}>
                Evaluations
              </Link>
            </ShowFor>
          </div>
          {tab !== 'overview' && hasRole(role, ['controller']) && <RunToolbar run={run.data} />}
          <nav aria-label="Run sections" className="tabs">
            <ul>
              {TABS.map(([t, label]) => (
                <li key={t}>
                  <Link to={tabHref(t)} aria-current={tab === t ? 'page' : undefined}>
                    {label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
          <div className="tab-panel">
            {tab === 'overview' &&
              (hasRole(role, ['controller']) ? (
                <>
                  <RunControls run={run.data} />
                  {snapshot ? (
                    <InjectForm runId={runId} mailboxes={snapshot.mailboxes} />
                  ) : state.isError ? (
                    <ErrorDisplay error={state.error} title="Could not load mailboxes for injection" />
                  ) : (
                    <Loading label="Loading mailboxes…" />
                  )}
                </>
              ) : (
                <p className="notice">Run controls (start, cancel, reset, replay, clock, inject) require a controller key.</p>
              ))}
            {tab === 'mailboxes' && (
              <MailboxBrowser
                run={run.data}
                snapshot={snapshot}
                error={state.error}
                selection={{ mailbox: params.get('mailbox'), folder: params.get('folder'), message: params.get('message') }}
                onChange={(n) => go({ mailbox: n.mailbox, folder: n.folder, message: n.message })}
              />
            )}
            {tab === 'activity' && <ActivityPanel key={run.data.id} run={run.data} />}
            {tab === 'trace' && <EventTimeline runId={runId} highlightSeq={seq && /^\d+$/.test(seq) ? Number(seq) : null} />}
            {tab === 'actions' && <ActionsTable runId={runId} highlight={params.get('action')} />}
            {tab === 'artifacts' && <ArtifactsTable runId={runId} highlight={params.get('ref')} />}
          </div>
        </>
      )}
    </>
  );
}
