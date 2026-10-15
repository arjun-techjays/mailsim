import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import type { AgentInterface } from '../api/types';
import { useApi } from '../auth/session';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { useAgentKeys } from './agentKeys';
import { INTERFACE_LABEL } from './snippets';
import { testHint } from './testHint';

/** "Test connection": the API server makes a real call through the interface with the key. */
export function TestConnection({ runId, iface }: { runId: string; iface: AgentInterface }) {
  const api = useApi();
  const held = useAgentKeys().get(runId);
  const [needsKey, setNeedsKey] = useState(false);
  const test = useMutation({
    mutationFn: (apiKey: string) => api.testConnection(runId, { interface: iface, api_key: apiKey }),
    gcTime: 0,
  });
  const label = INTERFACE_LABEL[iface];
  const run = () => {
    if (!held) {
      setNeedsKey(true);
      return;
    }
    setNeedsKey(false);
    test.mutate(held.apiKey);
  };
  const r = test.data;
  return (
    <div className="test-connection" data-testid={`test-${iface}`}>
      <button type="button" onClick={run} disabled={test.isPending}>
        {test.isPending ? `Testing ${label}…` : `Test ${label} connection`}
      </button>
      {needsKey && !held && (
        <p role="alert" className="field-error">
          Testing needs the agent key. Paste your saved key or mint a new one in{' '}
          <a href="#agent-key">Agent key</a>.
        </p>
      )}
      {test.isError && <ErrorDisplay error={test.error} title={`Could not run the ${label} test`} />}
      {r &&
        (r.ok ? (
          <div role="status" className="ok-box" data-testid={`test-ok-${iface}`}>
            <p className="ok-title">{label}: connected</p>
            <p>{r.summary}</p>
            {r.status_code != null && <p className="hint">Status {r.status_code}</p>}
          </div>
        ) : (
          <div role="alert" className="error-box" data-testid={`test-fail-${iface}`}>
            <p className="error-title">
              {label}: failed{r.error_code ? ` (${r.error_code}${r.status_code != null ? `, ${r.status_code}` : ''})` : ''}
            </p>
            <p>{r.summary}</p>
            {r.error_message && (
              <p>
                <code>{r.error_message}</code>
              </p>
            )}
            {Object.keys(r.details ?? {}).length > 0 && (
              <details>
                <summary>Details</summary>
                <pre>{JSON.stringify(r.details, null, 2)}</pre>
              </details>
            )}
            <p className="hint">{testHint(r)}</p>
          </div>
        ))}
    </div>
  );
}
