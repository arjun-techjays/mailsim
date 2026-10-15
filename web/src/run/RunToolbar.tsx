// Simulator clock controls shown above the run's tabs (controller/operator): advance virtual
// time (presets or custom) or run one scheduler tick, then say what happened — scheduled
// events that fired, persona replies and every message that was delivered — and refresh views.
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import type { ClockResult, RunOut } from '../api/types';
import { useApi } from '../auth/session';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { parseSnapshot } from '../mailbox/snapshot';
import { subjectOf } from '../mailbox/text';
import { formatVirtual } from '../mailbox/time';
import { deliveredIndex, type Delivered } from './toolbarDiff';
import { runKeys, runLinks } from './keys';
import '../mailbox/browser.css';

const PRESETS: { seconds: number; label: string; long: string }[] = [
  { seconds: 60, label: '+1 min', long: '1 minute' },
  { seconds: 600, label: '+10 min', long: '10 minutes' },
  { seconds: 3600, label: '+1 hour', long: '1 hour' },
  { seconds: 86400, label: '+1 day', long: '1 day' },
];

interface Outcome {
  label: string;
  before: string | null;
  result: ClockResult;
  delivered: Delivered[] | null;
}

function Result({ runId, o }: { runId: string; o: Outcome }) {
  const replies = o.result.persona_replies ?? 0;
  return (
    <div data-testid="toolbar-result">
      <p>
        <strong>{o.label}.</strong> Virtual time {o.before ? `${formatVirtual(o.before)} → ` : 'is now '}
        <strong>{formatVirtual(o.result.virtual_time)}</strong>.{' '}
        {o.result.fired.length > 0 ? `Scheduled events fired: ${o.result.fired.join(', ')}.` : 'No scheduled events were due.'}{' '}
        {replies} persona repl{replies === 1 ? 'y' : 'ies'} delivered.
      </p>
      {o.delivered && o.delivered.length > 0 && (
        <>
          <p>New messages:</p>
          <ul>
            {o.delivered.map((d) => (
              <li key={d.id}>
                <Link to={runLinks.message(runId, d.id)}>{subjectOf(d)}</Link> → {d.to}
              </li>
            ))}
          </ul>
        </>
      )}
      {o.delivered && o.delivered.length === 0 && <p className="muted">No new messages arrived.</p>}
    </div>
  );
}

export function RunToolbar({ run }: { run: RunOut }) {
  const api = useApi();
  const qc = useQueryClient();
  const [custom, setCustom] = useState('');
  const [customError, setCustomError] = useState('');
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const running = run.status === 'RUNNING';

  /** Messages currently in the run (null when the state cannot be read: the diff is skipped). */
  const messageIds = async (): Promise<Map<string, Delivered> | null> => {
    try {
      const raw = await qc.fetchQuery({ queryKey: runKeys.state(run.id), queryFn: () => api.state(run.id) });
      return deliveredIndex(parseSnapshot(raw));
    } catch {
      return null;
    }
  };

  const mutation = useMutation({
    mutationFn: async (op: { kind: 'advance'; seconds: number; label: string } | { kind: 'tick' }): Promise<Outcome> => {
      const before = await messageIds();
      const result = op.kind === 'tick' ? await api.tick(run.id) : await api.advance(run.id, op.seconds);
      await qc.invalidateQueries({ queryKey: runKeys.all(run.id) });
      const after = before ? await messageIds() : null;
      const delivered = before && after ? [...after.values()].filter((m) => !before.has(m.id)) : null;
      return {
        label: op.kind === 'tick' ? 'Scheduler tick' : `Advanced the clock by ${op.label}`,
        before: run.virtual_time ?? null,
        result,
        delivered,
      };
    },
    onMutate: () => setOutcome(null),
    onSuccess: (o) => setOutcome(o),
  });

  const onCustom = (e: FormEvent) => {
    e.preventDefault();
    const n = Number(custom);
    if (custom.trim() === '' || !Number.isFinite(n) || n <= 0) {
      setCustomError('Enter a number of seconds greater than 0.');
      return;
    }
    setCustomError('');
    mutation.mutate({ kind: 'advance', seconds: n, label: `${n} second${n === 1 ? '' : 's'}` });
  };

  const busy = mutation.isPending;
  const disabled = busy || !running;
  return (
    <section aria-labelledby="rt-h" className="rt-toolbar" data-testid="run-toolbar">
      <h2 id="rt-h">Simulator clock</h2>
      {!running && (
        <p className="hint rt-result" data-testid="toolbar-inactive">
          Clock controls need a RUNNING run; this run is {run.status}.
        </p>
      )}
      <div className="rt-group" role="group" aria-label="Advance virtual time">
        {PRESETS.map((p) => (
          <button
            key={p.seconds}
            type="button"
            disabled={disabled}
            aria-label={`Advance the clock ${p.long}`}
            onClick={() => mutation.mutate({ kind: 'advance', seconds: p.seconds, label: p.long })}
          >
            {p.label}
          </button>
        ))}
      </div>
      <form className="rt-group" onSubmit={onCustom} noValidate aria-label="Advance by a custom amount">
        <label htmlFor="rt-custom">Custom (seconds)</label>
        <input
          id="rt-custom"
          type="number"
          min={1}
          step="any"
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
          aria-invalid={!!customError || undefined}
          aria-describedby={customError ? 'rt-custom-err' : undefined}
          disabled={!running}
        />
        <button type="submit" disabled={disabled}>
          Advance
        </button>
        {customError && (
          <p id="rt-custom-err" className="field-error">
            {customError}
          </p>
        )}
      </form>
      <div className="rt-group">
        <button type="button" disabled={disabled} onClick={() => mutation.mutate({ kind: 'tick' })}>
          Tick scheduler
        </button>
        <span className="hint">Runs anything already due (persona replies, scheduled emails) without moving time.</span>
      </div>
      <div role="status" aria-live="polite" className="rt-result">
        {busy && <p>Working…</p>}
        {outcome && <Result runId={run.id} o={outcome} />}
      </div>
      {mutation.isError && (
        <div className="rt-result">
          <ErrorDisplay error={mutation.error} title="Clock operation failed" />
        </div>
      )}
    </section>
  );
}
