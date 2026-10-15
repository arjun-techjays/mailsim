import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { loadRecentRuns } from '../auth/storage';
import { Empty } from '../components/Status';
import { usePageTitle } from '../hooks/usePageTitle';

export function RunsPage() {
  usePageTitle('Runs');
  const navigate = useNavigate();
  const [runId, setRunId] = useState('');
  const [error, setError] = useState('');
  const recent = loadRecentRuns();

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!runId.trim()) {
      setError('Enter a run id.');
      return;
    }
    navigate(`/runs/${encodeURIComponent(runId.trim())}`);
  };

  return (
    <>
      <h1>Runs</h1>
      <p className="hint">API v1 has no run listing endpoint: open a run by id, or pick one opened in this tab.</p>
      <form className="form inline" onSubmit={onSubmit} noValidate>
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
      <h2>Recently opened in this tab</h2>
      {recent.length === 0 ? (
        <Empty>No runs opened yet.</Empty>
      ) : (
        <ul data-testid="recent-runs">
          {recent.map((r) => (
            <li key={r.id}>
              <Link to={`/runs/${encodeURIComponent(r.id)}`}>{r.id}</Link>
              {r.scenarioKey && <span className="muted"> ({r.scenarioKey})</span>}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
