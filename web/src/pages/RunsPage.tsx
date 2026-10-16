import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { isApiError } from '../api/client';
import type { RunOut } from '../api/types';
import { useApi } from '../auth/session';
import { loadRecentRuns } from '../auth/storage';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { PageIntro } from '../components/PageIntro';
import { WhatIsThis } from '../guide/Concepts';
import { Badge, Empty, Loading } from '../components/Status';
import { useCursorList } from '../hooks/useCursorList';
import { usePageTitle } from '../hooks/usePageTitle';

const runHref = (id: string) => `/runs/${encodeURIComponent(id)}`;

function RecentRuns() {
  const recent = loadRecentRuns();
  return (
    <>
      <h2>Recently opened in this tab</h2>
      {recent.length === 0 ? (
        <Empty>No runs opened yet.</Empty>
      ) : (
        <ul data-testid="recent-runs">
          {recent.map((r) => (
            <li key={r.id}>
              <Link to={runHref(r.id)}>{r.id}</Link>
              {r.scenarioKey && <span className="muted"> ({r.scenarioKey})</span>}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

/** GET /v1/runs is an additive endpoint; when absent (404/405/501) fall back to the tab's list. */
function RunList() {
  const api = useApi();
  const list = useCursorList<RunOut>(['runs'], (cursor) => api.listRuns(cursor));
  if (list.isLoading) return <Loading label="Loading runs…" />;
  if (list.error && list.items.length === 0) {
    const absent = isApiError(list.error) && [404, 405, 501].includes(list.error.status as number);
    return (
      <>
        {absent ? (
          <p className="hint" data-testid="runs-fallback">
            This API has no run listing endpoint: open a run by id, or pick one opened in this tab.
          </p>
        ) : (
          <ErrorDisplay error={list.error} title="Could not list runs" />
        )}
        <RecentRuns />
      </>
    );
  }
  return (
    <>
      <h2>Runs in this project</h2>
      {list.items.length === 0 ? (
        <Empty>
          No runs yet. <Link to="/scenarios">Pick a scenario</Link> and start a run; then connect your agent to it.
        </Empty>
      ) : (
        <table data-testid="runs-table">
          <thead>
            <tr>
              <th scope="col">Run</th>
              <th scope="col">Status</th>
              <th scope="col">Scenario</th>
              <th scope="col">Agent build</th>
              <th scope="col">Virtual time</th>
            </tr>
          </thead>
          <tbody>
            {list.items.map((r) => (
              <tr key={r.id}>
                <th scope="row">
                  <Link to={runHref(r.id)}>{r.id}</Link>
                  {r.replay_of && (
                    <>
                      {' '}
                      <span className="badge" data-testid="replay-badge">
                        Replay of {r.replay_of}
                      </span>
                    </>
                  )}
                </th>
                <td>
                  <Badge value={r.status} />
                </td>
                <td>{r.scenario_key ? `${r.scenario_key} v${r.scenario_version}` : '—'}</td>
                <td>{r.agent_build ?? '—'}</td>
                <td>{r.virtual_time ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {list.error && <ErrorDisplay error={list.error} title="Could not load more runs" />}
      {list.hasMore && (
        <button type="button" onClick={list.loadMore} disabled={list.isFetchingMore}>
          {list.isFetchingMore ? 'Loading…' : 'Load more runs'}
        </button>
      )}
    </>
  );
}

export function RunsPage() {
  usePageTitle('Runs');
  const navigate = useNavigate();
  const [runId, setRunId] = useState('');
  const [error, setError] = useState('');

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!runId.trim()) {
      setError('Enter a run id.');
      return;
    }
    navigate(runHref(runId.trim()));
  };

  return (
    <>
      <h1>Runs</h1>
      <PageIntro
        next={[
          { to: '/scenarios', label: 'Start a new run from a scenario' },
          { to: '/', label: 'Getting started checklist' },
        ]}
      >
        <p>
          Every run is an isolated execution of a scenario version with its own mailboxes, virtual clock and event log.
          Open one to connect your agent, advance time, inspect mailboxes and evaluate it.
        </p>
        <WhatIsThis term="run" />
      </PageIntro>
      <form className="form inline" onSubmit={onSubmit} noValidate aria-label="Open run by id">
        <div className="field">
          <label htmlFor="run-id">Run id</label>
          <input
            id="run-id"
            value={runId}
            onChange={(e) => setRunId(e.target.value)}
            aria-invalid={!!error}
            aria-describedby={error ? 'run-id-err' : undefined}
          />
          {error && (
            <p id="run-id-err" className="field-error">
              {error}
            </p>
          )}
        </div>
        <button type="submit">Open run</button>
      </form>
      <RunList />
    </>
  );
}
