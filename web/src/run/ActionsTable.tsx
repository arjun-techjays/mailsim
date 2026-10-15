import { useQuery } from '@tanstack/react-query';
import { useApi } from '../auth/session';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { JsonBlock } from '../components/JsonBlock';
import { Empty, Loading } from '../components/Status';
import { runKeys } from './keys';

/** `highlight` is an evidence ref; ActionOut has no id, so a numeric ref is treated as a 1-based index. */
export function ActionsTable({ runId, highlight }: { runId: string; highlight: string | null }) {
  const api = useApi();
  const q = useQuery({ queryKey: runKeys.actions(runId), queryFn: () => api.actions(runId) });
  if (q.isPending) return <Loading label="Loading actions…" />;
  if (q.isError) return <ErrorDisplay error={q.error} title="Could not load actions" />;
  return (
    <section aria-labelledby="actions-h">
      <h2 id="actions-h">Agent actions</h2>
      {highlight && (
        <p className="notice">
          Evidence reference <code>{highlight}</code> (actions have no stable id in API v1; numeric references are
          matched by position).
        </p>
      )}
      {q.data.length === 0 ? (
        <Empty>No actions recorded.</Empty>
      ) : (
        <table data-testid="actions-table">
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Actor</th>
              <th scope="col">Operation</th>
              <th scope="col">Outcome</th>
              <th scope="col">Input</th>
              <th scope="col">Result</th>
            </tr>
          </thead>
          <tbody>
            {q.data.map((a, i) => (
              <tr key={i} className={highlight === String(i + 1) ? 'highlight' : undefined}>
                <th scope="row">{i + 1}</th>
                <td>{a.actor}</td>
                <td>{a.operation}</td>
                <td>{a.outcome}</td>
                <td>
                  <JsonBlock value={a.input} label="Input" />
                </td>
                <td>
                  <JsonBlock value={a.result} label="Result" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
