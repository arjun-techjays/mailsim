import { useEffect, useMemo, useRef, useState } from 'react';
import type { EventOut } from '../api/types';
import { useApi } from '../auth/session';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { JsonBlock } from '../components/JsonBlock';
import { Empty, Loading } from '../components/Status';
import { useCursorList } from '../hooks/useCursorList';
import { runKeys } from './keys';

export const EVENT_PAGE_SIZE = 50;

const uniq = (values: string[]) => [...new Set(values)].sort();

/**
 * Event trace with cursor pagination. The v1 events endpoint has no server-side filters,
 * so type/actor/epoch filters apply to the pages loaded so far (stated in the UI).
 */
export function EventTimeline({ runId, highlightSeq }: { runId: string; highlightSeq: number | null }) {
  const api = useApi();
  const list = useCursorList<EventOut>(runKeys.events(runId), (cursor) => api.events(runId, cursor, EVENT_PAGE_SIZE));
  const [type, setType] = useState('');
  const [actor, setActor] = useState('');
  const [epoch, setEpoch] = useState('');
  const highlighted = useRef<HTMLTableRowElement>(null);

  const options = useMemo(
    () => ({
      types: uniq(list.items.map((e) => e.type)),
      actors: uniq(list.items.map((e) => e.actor)),
      epochs: uniq(list.items.map((e) => String(e.epoch))),
    }),
    [list.items],
  );
  const shown = list.items.filter(
    (e) => (!type || e.type === type) && (!actor || e.actor === actor) && (!epoch || String(e.epoch) === epoch),
  );
  const highlightLoaded = highlightSeq != null && list.items.some((e) => e.sequence === highlightSeq);

  useEffect(() => {
    if (highlightLoaded) highlighted.current?.focus();
  }, [highlightLoaded]);

  if (list.isLoading) return <Loading label="Loading events…" />;
  if (list.error && list.items.length === 0) return <ErrorDisplay error={list.error} title="Could not load events" />;

  const select = (id: string, label: string, value: string, set: (v: string) => void, values: string[]) => (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value} onChange={(e) => set(e.target.value)}>
        <option value="">All</option>
        {values.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </select>
    </div>
  );

  return (
    <section aria-labelledby="trace-h">
      <h2 id="trace-h">Event trace</h2>
      <div className="form inline" role="group" aria-label="Filter events">
        {select('ev-type', 'Type', type, setType, options.types)}
        {select('ev-actor', 'Actor', actor, setActor, options.actors)}
        {select('ev-epoch', 'Epoch', epoch, setEpoch, options.epochs)}
      </div>
      <p className="hint" role="status">
        Showing {shown.length} of {list.items.length} loaded event(s){list.hasMore ? '; more pages available' : ''}.
        Filters apply to loaded pages.
      </p>
      {highlightSeq != null && !highlightLoaded && (
        <p className="notice">Event #{highlightSeq} is not loaded yet. Use “Load more events”.</p>
      )}
      {shown.length === 0 ? (
        <Empty>No events match.</Empty>
      ) : (
        <table data-testid="event-table">
          <thead>
            <tr>
              <th scope="col">Seq</th>
              <th scope="col">Epoch</th>
              <th scope="col">Virtual time</th>
              <th scope="col">Type</th>
              <th scope="col">Actor</th>
              <th scope="col">Operation</th>
              <th scope="col">Outcome</th>
              <th scope="col">Payload</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((e) => {
              const hl = e.sequence === highlightSeq;
              return (
                <tr
                  key={`${e.epoch}-${e.sequence}`}
                  id={`event-${e.sequence}`}
                  ref={hl ? highlighted : undefined}
                  tabIndex={hl ? -1 : undefined}
                  className={hl ? 'highlight' : undefined}
                  data-testid={hl ? 'highlighted-event' : undefined}
                >
                  <th scope="row">{e.sequence}</th>
                  <td>{e.epoch}</td>
                  <td>{e.virtual_time ?? '—'}</td>
                  <td>{e.type}</td>
                  <td>{e.actor}</td>
                  <td>{e.operation}</td>
                  <td>{e.outcome}</td>
                  <td>
                    <JsonBlock value={e.payload} label="Payload" />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {list.error && <ErrorDisplay error={list.error} title="Could not load more events" />}
      {list.hasMore && (
        <button type="button" onClick={list.loadMore} disabled={list.isFetchingMore}>
          {list.isFetchingMore ? 'Loading…' : 'Load more events'}
        </button>
      )}
    </section>
  );
}
