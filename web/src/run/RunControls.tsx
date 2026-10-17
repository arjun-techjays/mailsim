import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { isApiError } from '../api/client';
import type { ClockResult, ReplayOut, RunOut } from '../api/types';
import { useApi } from '../auth/session';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { runKeys } from './keys';
import { canDo, stateHint } from './runState';
import { clockCall } from './simActions';

type Op = 'start' | 'cancel' | 'reset' | 'replay' | 'tick' | 'advance';

type Outcome =
  | { kind: 'run'; label: string; run: RunOut }
  | { kind: 'clock'; label: string; result: ClockResult }
  | { kind: 'replay'; result: ReplayOut }
  | { kind: 'note'; text: string };

const LABELS: Record<Op, string> = {
  start: 'start',
  cancel: 'cancel',
  reset: 'reset',
  replay: 'replay',
  tick: 'run a scheduler tick on',
  advance: 'advance the clock of',
};

const isConflict = (e: unknown) => isApiError(e) && e.status === 409;

function OutcomeView({ outcome }: { outcome: Outcome }) {
  if (outcome.kind === 'note') return <p data-testid="control-note">{outcome.text}</p>;
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
        {outcome.result.fired.length > 0 ? `fired ${outcome.result.fired.join(', ')}` : 'no scheduled events fired'};{' '}
        <span data-testid="persona-replies">
          {outcome.result.persona_replies ?? 0} persona repl{(outcome.result.persona_replies ?? 0) === 1 ? 'y' : 'ies'} delivered
        </span>
        .
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
  const [confirm, setConfirm] = useState<'cancel' | 'reset' | null>(null);
  const opener = useRef<HTMLButtonElement | null>(null);
  const resultRegion = useRef<HTMLDivElement>(null);
  /** After a confirmed cancel/reset the dialog (and its buttons) is gone: continue at the result. */
  const confirmed = (op: 'cancel' | 'reset') => {
    setConfirm(null);
    mutation.mutate(op);
    resultRegion.current?.focus();
  };

  const mutation = useMutation({
    mutationFn: async (op: Op): Promise<Outcome> => {
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
          return { kind: 'clock', label: 'Scheduler tick', result: await clockCall(api, run.id, { kind: 'tick' }) };
        default:
          return {
            kind: 'clock',
            label: 'Clock advanced',
            result: await clockCall(api, run.id, { kind: 'advance', seconds: Number(seconds) }),
          };
      }
    },
    onMutate: () => setOutcome(null),
    onSuccess: (o) => setOutcome(o),
    onError: (e, op) => {
      // The run changed state elsewhere (another tab, the agent, an evaluation): explain it
      // instead of leaving a raw 409; the refetch below updates which controls are offered.
      if (isConflict(e)) {
        setOutcome({
          kind: 'note',
          text: `Could not ${LABELS[op]} the run: ${e.message}. The run’s state was refreshed and the controls now match it.`,
        });
      }
    },
    onSettled: () => qc.invalidateQueries({ queryKey: runKeys.all(run.id) }),
  });

  // A new state (or epoch) makes earlier errors stale.
  const { reset: clearError } = mutation;
  useEffect(() => {
    clearError();
  }, [run.status, run.epoch, clearError]);

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
  const ask = (op: 'cancel' | 'reset', el: HTMLButtonElement) => {
    opener.current = el;
    setConfirm(op);
  };
  const closeConfirm = () => {
    setConfirm(null);
    opener.current?.focus();
  };

  const busy = mutation.isPending;
  const off = (action: Parameters<typeof canDo>[1]) => busy || !canDo(run, action);
  return (
    <section aria-labelledby="controls-h" className="card">
      <h2 id="controls-h">Run controls</h2>
      <p className="hint" id="controls-state" data-testid="controls-state">
        {stateHint(run)}
      </p>
      <div className="row" role="group" aria-label="Lifecycle" aria-describedby="controls-state">
        <button type="button" disabled={off('start')} onClick={() => mutation.mutate('start')}>
          Start
        </button>
        <button type="button" disabled={off('cancel')} onClick={(e) => ask('cancel', e.currentTarget)}>
          Cancel run
        </button>
        <button type="button" disabled={off('reset')} onClick={(e) => ask('reset', e.currentTarget)}>
          Reset (new epoch)
        </button>
        <button type="button" disabled={off('replay')} onClick={() => mutation.mutate('replay')}>
          Replay &amp; compare hash
        </button>
        <button type="button" disabled={off('clock')} onClick={() => mutation.mutate('tick')}>
          Scheduler tick
        </button>
      </div>
      {confirm === 'cancel' && (
        <ConfirmDialog
          title="Cancel this run?"
          confirmLabel="Cancel run"
          cancelLabel="Keep running"
          busy={busy}
          onCancel={closeConfirm}
          onConfirm={() => confirmed('cancel')}
        >
          <p>
            The run becomes CANCELLED for good: the agent can no longer act in it and the clock stops. Its events stay
            recorded, so it can still be replayed and evaluated.
          </p>
        </ConfirmDialog>
      )}
      {confirm === 'reset' && (
        <ConfirmDialog
          title="Reset this run to its starting state?"
          confirmLabel="Reset run"
          cancelLabel="Keep the current state"
          busy={busy}
          onCancel={closeConfirm}
          onConfirm={() => confirmed('reset')}
        >
          <p>
            Every mailbox goes back to the scenario’s starting point in a new epoch (epoch {run.epoch + 1}). What the
            agent did so far stays in the event log but no longer counts for this epoch.
          </p>
        </ConfirmDialog>
      )}
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
        <button type="submit" disabled={off('clock')}>
          Advance clock
        </button>
      </form>
      <div role="status" aria-live="polite" aria-label="Result" tabIndex={-1} ref={resultRegion} data-testid="control-result">
        {busy && <p>Working…</p>}
        {outcome && <OutcomeView outcome={outcome} />}
      </div>
      {mutation.isError && !isConflict(mutation.error) && <ErrorDisplay error={mutation.error} title="Operation failed" />}
    </section>
  );
}
