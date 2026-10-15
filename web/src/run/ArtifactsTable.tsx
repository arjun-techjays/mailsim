import { useQuery } from '@tanstack/react-query';
import { useApi } from '../auth/session';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { bytes } from '../components/format';
import { Empty, Loading } from '../components/Status';
import { runKeys } from './keys';

export function ArtifactsTable({ runId, highlight }: { runId: string; highlight: string | null }) {
  const api = useApi();
  const q = useQuery({ queryKey: runKeys.artifacts(runId), queryFn: () => api.artifacts(runId) });
  if (q.isPending) return <Loading label="Loading artifacts…" />;
  if (q.isError) return <ErrorDisplay error={q.error} title="Could not load artifacts" />;
  return (
    <section aria-labelledby="artifacts-h">
      <h2 id="artifacts-h">Artifacts</h2>
      <p className="hint">API v1 lists artifact references but has no download endpoint; refs are shown for lookup.</p>
      {q.data.length === 0 ? (
        <Empty>No artifacts yet. Raw messages, attachments and traces appear here as the run produces them.</Empty>
      ) : (
        <table data-testid="artifacts-table">
          <thead>
            <tr>
              <th scope="col">Reference</th>
              <th scope="col">Kind</th>
              <th scope="col">Content type</th>
              <th scope="col">Size</th>
            </tr>
          </thead>
          <tbody>
            {q.data.map((a) => (
              <tr
                key={`${a.kind}:${a.ref}`}
                className={highlight === a.ref ? 'highlight' : undefined}
                data-testid={highlight === a.ref ? 'highlighted-artifact' : undefined}
              >
                <th scope="row">
                  <code>{a.ref}</code>
                </th>
                <td>{a.kind}</td>
                <td>{a.content_type}</td>
                <td>{bytes(a.size)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
