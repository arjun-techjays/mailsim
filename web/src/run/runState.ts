// Which operator actions make sense in which run state (engine/domain/states.py): the server
// stays the enforcement point, the dashboard only stops offering calls that must fail.
import type { RunOut } from '../api/types';

export const TERMINAL_STATUSES: readonly string[] = ['PASSED', 'FAILED', 'ERROR', 'CANCELLED'];

export type RunAction = 'start' | 'cancel' | 'reset' | 'replay' | 'clock' | 'inject';

export function isTerminal(status: string): boolean {
  return TERMINAL_STATUSES.includes(status);
}

/** Whether `action` can succeed on a run in `status` (READY: start, cancel, reset; RUNNING: all but start). */
export function canDo(status: string, action: RunAction): boolean {
  switch (action) {
    case 'replay':
      return true;
    case 'start':
      return status === 'READY';
    case 'cancel':
    case 'reset':
      return status === 'READY' || status === 'RUNNING';
    default:
      return status === 'RUNNING';
  }
}

/** One sentence on what the run's state allows, for the controls. */
export function stateHint(status: string): string {
  if (status === 'READY') {
    return 'This run is READY: start it so the agent can work and the clock can move. Cancel and reset are also available.';
  }
  if (status === 'RUNNING') {
    return 'This run is RUNNING: advance the clock, inject mail, reset or cancel it. Start is not needed again.';
  }
  if (isTerminal(status)) {
    return `This run is ${status} and cannot change any more: only replay (and evaluation) remain.`;
  }
  return `This run is ${status}: it is not ready yet, so only replay is available.`;
}

/**
 * The run this one replays, when it is a replay (RunOut.replay_of; a `kind: "replay"` without
 * the id still counts). Replays verify determinism and are not graded.
 */
export function replayOf(run: RunOut): string | null | false {
  const kind = (run as RunOut & { kind?: unknown }).kind;
  if (run.replay_of) return run.replay_of;
  return kind === 'replay' ? null : false;
}

export function isReplayRun(run: RunOut): boolean {
  return replayOf(run) !== false;
}
