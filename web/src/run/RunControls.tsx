import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import type { ClockResult, ReplayOut, RunOut } from '../api/types';
import { useApi } from '../auth/session';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { runKeys } from './keys';

type Outcome =
  | { kind: 'run'; label: string; run: RunOut }
  | { kind: 'clock'; label: string; result: ClockResult }
  | { kind: 'replay'; result: ReplayOut };

function OutcomeView({ outcome }: { outcome: Outcome }) {
  if (outcome.kind === 'run') {
    return (
      <p>
        {outcome.label}: run is now <strong>{outcome.run.status}</strong> (epoch {outcome.run.epoch}).
      </p>
    );
  }
  if (outcome.kind === 'clock') {
    return (
      <p>
        {outcome.label}: virtual time is now <strong>{outcome.result.virtual_time}</strong>;{' '}
        {outcome.result.fired.length > 0 ? `fired ${outcome.result.fired.join(', ')}` : 'no scheduled events fired'}.
      </p>
    );
  }
  const r = outcome.result;
  return (
    <div data-testid="replay-result">
      <p>
        Replay {r.matches ? 'matches' : 'does NOT match'}: state hash{' '}
        <strong>{r.matches ? 'identical' : 'differs'}</strong> over {r.event_count} events.
      </p>
      <dl className="kv">
        <dt>Original hash</dt>
        <dd>
          <code>{r.original_hash}</code>
        </dd>
        <dt>Replay hash</dt>
        <dd>
          <code>{r.replay_hash}</code>
        </dd>
        <dt>Replay run</dt>
        <dd>
          <Link to={`/runs/${encodeURIComponent(r.replay_run_id)}`}>{r.replay_run_id}</Link>
        </dd>
        <dt>Missing blobs</dt>
        <dd>{r.missing_blobs.length > 0 ? r.missing_blobs.join(', ') : 'none'}</dd>
      </dl>
    </div>
  );
}

export function RunControls({ run }: { run: RunOut }) {
  const api = useApi();
  const qc = useQueryClient();
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [seconds, setSeconds] = useState('60');
  const [secondsError, setSecondsError] = useState('');

  const mutation = useMutation({
    mutationFn: async (op: 'start' | 'cancel' | 'reset' | 'replay' | 'tick' | 'advance'): Promise<Outcome> => {
      switch (op) {
        case 'start':
          return { kind: 'run', label: 'Started', run: await api.startRun(run.id) };
        case 'cancel':
          return { kind: 'run', label: 'Cancelled', run: await api.cancelRun(run.id) };
        case 'reset':
          return { kind: 'run', label: 'Reset', run: await api.resetRun(run.id) };
        case 'replay':
          return { kind: 'replay', result: await api.replayRun(run.id) };
        case 'tick':
          return { kind: 'clock', label: 'Scheduler tick', result: await api.tick(run.id) };
        default:
          return { kind: 'clock', label: 'Clock advanced', result: await api.advance(run.id, Number(seconds)) };
      }
    },
    onMutate: () => setOutcome(null),
    onSuccess: (o) => setOutcome(o),
    onSettled: () => qc.invalidateQueries({ queryKey: runKeys.all(run.id) }),
  });

  const onAdvance = (e: FormEvent) => {
    e.preventDefault();
    const n = Number(seconds);
    if (seconds.trim() === '' || !Number.isFinite(n) || n < 0) {
      setSecondsError('Enter a number of seconds (0 or more).');
      return;
    }
    setSecondsError('');
    mutation.mutate('advance');
  };

  const busy = mutation.isPending;
  return (
    <section aria-labelledby="controls-h" className="card">
      <h2 id="controls-h">Run controls</h2>
      <div className="row" role="group" aria-label="Lifecycle">
        <button type="button" disabled={busy} onClick={() => mutation.mutate('start')}>
          Start
        </button>
        <button type="button" disabled={busy} onClick={() => mutation.mutate('cancel')}>
          Cancel run
        </button>
        <button type="button" disabled={busy} onClick={() => mutation.mutate('reset')}>
          Reset (new epoch)
        </button>
        <button type="button" disabled={busy} onClick={() => mutation.mutate('replay')}>
          Replay &amp; compare hash
        </button>
        <button type="button" disabled={busy} onClick={() => mutation.mutate('tick')}>
          Scheduler tick
        </button>
      </div>
      <form className="form inline" onSubmit={onAdvance} aria-label="Advance virtual clock" noValidate>
        <div className="field">
          <label htmlFor="adv-seconds">Advance clock by (seconds)</label>
          <input
            id="adv-seconds"
            type="number"
            min={0}
            step="any"
            value={seconds}
            onChange={(e) => setSeconds(e.target.value)}
            aria-invalid={!!secondsError}
            aria-describedby={secondsError ? 'adv-error' : undefined}
          />
          {secondsError && (
            <p id="adv-error" className="field-error">
              {secondsError}
            </p>
          )}
        </div>
        <button type="submit" disabled={busy}>
          Advance clock
        </button>
      </form>
      <div role="status" aria-live="polite" data-testid="control-result">
        {busy && <p>Working…</p>}
        {outcome && <OutcomeView outcome={outcome} />}
      </div>
      {mutation.isError && <ErrorDisplay error={mutation.error} title="Operation failed" />}
    </section>
  );
}
