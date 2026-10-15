// What the agent (and the simulator) did, newest first. While the run is RUNNING it polls
// GET /v1/runs/{id}/activity?after_id=... every few seconds; polling pauses on request, while
// the browser tab is hidden, and stops once the run is no longer running. New rows are
// announced politely to screen readers, at most once per ANNOUNCE_MS.
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ActivityItem, RunOut } from '../api/types';
import { useApi } from '../auth/session';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { JsonBlock } from '../components/JsonBlock';
import { Badge, Empty, Loading } from '../components/Status';
import { formatVirtual } from '../mailbox/time';
import { interfaceLabel, outcomeKind, summarizeInput } from './activity';
import { runKeys } from './keys';
import '../mailbox/browser.css';

const PAGE = 200;
const MAX_PAGES_PER_POLL = 10;

function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden');
  useEffect(() => {
    const on = () => setVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', on);
    return () => document.removeEventListener('visibilitychange', on);
  }, []);
  return visible;
}

export function ActivityPanel({
  run,
  pollMs = 3000,
  announceMs = 10_000,
}: {
  run: RunOut;
  pollMs?: number;
  announceMs?: number;
}) {
  const api = useApi();
  const qc = useQueryClient();
  const visible = useDocumentVisible();
  const [items, setItems] = useState<ActivityItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [paused, setPaused] = useState(false);
  const [actor, setActor] = useState('');
  const [announcement, setAnnouncement] = useState('');
  const after = useRef(0);
  const inFlight = useRef(false);
  const initial = useRef(true);
  const pending = useRef<ActivityItem[]>([]);
  const lastAnnounce = useRef(0);
  const announceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const announce = useCallback(() => {
    announceTimer.current = null;
    const list = pending.current;
    pending.current = [];
    lastAnnounce.current = Date.now();
    const latest = list[list.length - 1] as ActivityItem;
    setAnnouncement(
      `${list.length} new action${list.length === 1 ? '' : 's'}. Latest: ${latest.actor} ${latest.operation} (${latest.outcome}) via ${interfaceLabel(latest.interface)}.`,
    );
  }, []);

  const fetchNew = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const fresh: ActivityItem[] = [];
      for (let i = 0; i < MAX_PAGES_PER_POLL; i += 1) {
        const page = await api.activity(run.id, after.current, PAGE);
        fresh.push(...page.items);
        if (page.next_after_id != null) after.current = page.next_after_id;
        if (page.items.length < PAGE) break;
      }
      setError(null);
      if (fresh.length > 0) {
        setItems((prev) => [...prev, ...fresh]);
        if (!initial.current) {
          pending.current.push(...fresh);
          const wait = lastAnnounce.current + announceMs - Date.now();
          if (wait <= 0) announce();
          else announceTimer.current ??= setTimeout(announce, wait);
          // the run summary (agent actions, virtual time) moved on too
          void qc.invalidateQueries({ queryKey: runKeys.all(run.id), exact: true });
        }
      }
    } catch (e) {
      setError(e);
    } finally {
      initial.current = false;
      inFlight.current = false;
      setLoading(false);
    }
  }, [api, run.id, announce, announceMs, qc]);

  useEffect(() => {
    void fetchNew();
  }, [fetchNew]);

  const running = run.status === 'RUNNING';
  const live = running && !paused && visible;
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => void fetchNew(), pollMs);
    return () => clearInterval(t);
  }, [live, pollMs, fetchNew]);
  useEffect(
    () => () => {
      if (announceTimer.current) clearTimeout(announceTimer.current);
    },
    [],
  );

  const actors = [...new Set(items.map((i) => i.actor))].sort();
  const shown = items.filter((i) => !actor || i.actor === actor).reverse();
  const status = !running
    ? `Not live: the run is ${run.status}, so nothing new will happen.`
    : paused
      ? 'Paused. Resume to keep updating.'
      : !visible
        ? 'Paused while this browser tab is hidden.'
        : `Live: checking for new activity every ${Math.round(pollMs / 100) / 10} s.`;

  return (
    <section aria-labelledby="activity-h">
      <h2 id="activity-h">Activity</h2>
      <p className="hint">
        Every operation the agent made, newest first: when (virtual time), what, whether it worked, and which interface it
        came through (REST, Gmail, Graph, MCP, IMAP or SMTP). Simulator and controller actions (persona replies, injected
        emails) appear too. Open the Mailboxes tab to see the resulting messages.
      </p>
      <div className="act-controls">
        {running && (
          <button type="button" aria-pressed={paused} onClick={() => setPaused(!paused)}>
            {paused ? 'Resume live updates' : 'Pause live updates'}
          </button>
        )}
        <button type="button" onClick={() => void fetchNew()}>
          Check now
        </button>
        <div className="field inline">
          <label htmlFor="act-actor">Show</label>
          <select id="act-actor" value={actor} onChange={(e) => setActor(e.target.value)}>
            <option value="">Everyone</option>
            {actors.map((a) => (
              <option key={a} value={a}>
                {a} only
              </option>
            ))}
          </select>
        </div>
        <span className="muted" data-testid="activity-status">
          {status}
        </span>
      </div>
      <div aria-live="polite" role="status" className="visually-hidden" data-testid="activity-announcer">
        {announcement}
      </div>
      {error != null && <ErrorDisplay error={error} title="Could not load activity" />}
      {loading ? (
        <Loading label="Loading activity…" />
      ) : shown.length === 0 ? (
        <Empty>No activity yet. Connect your agent and start the run; its actions appear here.</Empty>
      ) : (
        <table data-testid="activity-table" className="act-table">
          <caption>
            {shown.length} action{shown.length === 1 ? '' : 's'}, newest first
          </caption>
          <thead>
            <tr>
              <th scope="col">Time (virtual)</th>
              <th scope="col">Actor</th>
              <th scope="col">Operation</th>
              <th scope="col">Outcome</th>
              <th scope="col">Interface</th>
              <th scope="col">Input</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((a) => (
              <tr key={a.id}>
                <td>{formatVirtual(a.virtual_time)}</td>
                <td>{a.actor}</td>
                <th scope="row">{a.operation}</th>
                <td>
                  <Badge value={a.outcome} kind={outcomeKind(a.outcome)} />
                </td>
                <td className="act-iface">
                  <span className="badge" data-testid="interface-badge">
                    {interfaceLabel(a.interface)}
                  </span>
                </td>
                <td className="act-summary">
                  <span>{summarizeInput(a.input) || '—'}</span>
                  {Object.keys(a.result).length > 0 && <JsonBlock value={a.result} label="Result" />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
