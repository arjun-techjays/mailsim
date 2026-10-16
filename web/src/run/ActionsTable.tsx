import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { useApi } from '../auth/session';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { JsonBlock } from '../components/JsonBlock';
import { Empty, Loading } from '../components/Status';
import { runKeys } from './keys';

/**
 * The event range an action produced: `#5`, `#5–#7`, or "none" when it produced no events (the
 * server then reports the next free sequence as start and the last used one as end, e.g. 18–17).
 */
function eventRange(start: number | null | undefined, end: number | null | undefined): string {
  if (start == null) return '—';
  const last = end ?? start;
  if (last < start) return 'none';
  return last === start ? `#${start}` : `#${start}–#${last}`;
}

/** `highlight` is an evidence ref. Rows are matched by action id when the API provides one. */
export function ActionsTable({ runId, highlight }: { runId: string; highlight: string | null }) {
  const api = useApi();
  const q = useQuery({ queryKey: runKeys.actions(runId), queryFn: () => api.actions(runId) });
  const row = useRef<HTMLTableRowElement>(null);
  const matched = !!highlight && !!q.data?.some((a) => a.id != null && String(a.id) === highlight);
  useEffect(() => {
    if (matched) row.current?.focus();
  }, [matched]);

  if (q.isPending) return <Loading label="Loading actions…" />;
  if (q.isError) return <ErrorDisplay error={q.error} title="Could not load actions" />;
  return (
    <section aria-labelledby="actions-h">
      <h2 id="actions-h">Agent actions</h2>
      {highlight && !matched && (
        <p className="notice" data-testid="unmatched-action">
          Evidence reference <code>{highlight}</code> does not match an action id in this run.
        </p>
      )}
      {q.data.length === 0 ? (
        <Empty>No actions recorded.</Empty>
      ) : (
        <table data-testid="actions-table">
          <thead>
            <tr>
              <th scope="col">Action</th>
              <th scope="col">Actor</th>
              <th scope="col">Operation</th>
              <th scope="col">Outcome</th>
              <th scope="col">Virtual time</th>
              <th scope="col">Events</th>
              <th scope="col">Input</th>
              <th scope="col">Result</th>
            </tr>
          </thead>
          <tbody>
            {q.data.map((a, i) => {
              const hl = matched && a.id != null && String(a.id) === highlight;
              return (
                <tr
                  key={a.id ?? i}
                  ref={hl ? row : undefined}
                  tabIndex={hl ? -1 : undefined}
                  className={hl ? 'highlight' : undefined}
                  data-testid={hl ? 'highlighted-action' : undefined}
                >
                  <th scope="row">{a.id ?? i + 1}</th>
                  <td>{a.actor}</td>
                  <td>{a.operation}</td>
                  <td>{a.outcome}</td>
                  <td>{a.virtual_time ?? '—'}</td>
                  <td>{eventRange(a.seq_start, a.seq_end)}</td>
                  <td>
                    <JsonBlock value={a.input} label="Input" />
                  </td>
                  <td>
                    <JsonBlock value={a.result} label="Result" />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
