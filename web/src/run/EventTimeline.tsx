import { useEffect, useRef, useState } from 'react';
import type { EventOut } from '../api/types';
import { useApi } from '../auth/session';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { JsonBlock } from '../components/JsonBlock';
import { Empty, Loading } from '../components/Status';
import { useCursorList } from '../hooks/useCursorList';
import { runKeys } from './keys';

export const EVENT_PAGE_SIZE = 50;

/**
 * Event trace with cursor pagination. type/actor/epoch filters are sent to the server (additive
 * query parameters) and also applied to the received pages, so the view is correct whether or
 * not the server supports them. Filter options accumulate from every page seen.
 */
export function EventTimeline({ runId, highlightSeq }: { runId: string; highlightSeq: number | null }) {
  const api = useApi();
  const [type, setType] = useState('');
  const [actor, setActor] = useState('');
  const [epoch, setEpoch] = useState('');
  const filters = { type: type || undefined, actor: actor || undefined, epoch: epoch || undefined };
  const list = useCursorList<EventOut>([...runKeys.events(runId), filters], (cursor) =>
    api.events(runId, cursor, EVENT_PAGE_SIZE, filters),
  );
  const highlighted = useRef<HTMLTableRowElement>(null);
  const seen = useRef({ types: new Set<string>(), actors: new Set<string>(), epochs: new Set<string>() });
  for (const e of list.items) {
    seen.current.types.add(e.type);
    seen.current.actors.add(e.actor);
    seen.current.epochs.add(String(e.epoch));
  }
  const options = {
    types: [...seen.current.types].sort(),
    actors: [...seen.current.actors].sort(),
    epochs: [...seen.current.epochs].sort(),
  };
  const shown = list.items.filter(
    (e) => (!type || e.type === type) && (!actor || e.actor === actor) && (!epoch || String(e.epoch) === epoch),
  );
  const highlightLoaded = highlightSeq != null && list.items.some((e) => e.sequence === highlightSeq);

  useEffect(() => {
    if (highlightLoaded) highlighted.current?.focus();
  }, [highlightLoaded]);

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
      {list.isLoading ? (
        <Loading label="Loading events…" />
      ) : list.error && list.items.length === 0 ? (
        <ErrorDisplay error={list.error} title="Could not load events" />
      ) : (
        <p className="hint" role="status">
          Showing {shown.length} of {list.items.length} loaded event(s){list.hasMore ? '; more pages available' : ''}.
        </p>
      )}
      {highlightSeq != null && !highlightLoaded && !list.isLoading && (
        <p className="notice">Event #{highlightSeq} is not loaded yet. Use “Load more events”.</p>
      )}
      {list.isLoading || (list.error && list.items.length === 0) ? null : shown.length === 0 ? (
        <Empty>{type || actor || epoch ? 'No events match these filters.' : 'This run has no events yet. Start it, or advance the clock from the Overview tab.'}</Empty>
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
      {list.error && list.items.length > 0 && <ErrorDisplay error={list.error} title="Could not load more events" />}
      {list.hasMore && (
        <button type="button" onClick={list.loadMore} disabled={list.isFetchingMore}>
          {list.isFetchingMore ? 'Loading…' : 'Load more events'}
        </button>
      )}
    </section>
  );
}
