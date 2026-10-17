// Which operator actions make sense in which run state (engine/domain/states.py): the server
// stays the enforcement point, the dashboard only stops offering calls that must fail.
import type { RunOut } from '../api/types';

/** REPLAYED (QA-A3): the terminal status of a replay run. */
export const TERMINAL_STATUSES: readonly string[] = ['PASSED', 'FAILED', 'ERROR', 'CANCELLED', 'REPLAYED'];

export type RunAction = 'start' | 'cancel' | 'reset' | 'replay' | 'clock' | 'inject';

export function isTerminal(status: string): boolean {
  return TERMINAL_STATUSES.includes(status);
}

/**
 * Whether `action` can succeed on `run` (READY: start, cancel, reset; RUNNING: all but start;
 * terminal: replay only). A replay run (replay_of set) only offers replay, whatever status a
 * legacy replay was stored with.
 */
export function canDo(run: Pick<RunOut, 'status' | 'replay_of'>, action: RunAction): boolean {
  if (action === 'replay') return true;
  if (run.replay_of) return false;
  const { status } = run;
  switch (action) {
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
export function stateHint(run: Pick<RunOut, 'status' | 'replay_of'>): string {
  const { status } = run;
  if (status === 'REPLAYED' || run.replay_of) {
    return `This run is a replay (${status}): it only checks that the recorded events rebuild the same state, so only replay remains.`;
  }
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

/** Why mail cannot be injected into `run` (shown next to disabled inject controls). */
export function injectOffReason(run: Pick<RunOut, 'status' | 'replay_of'>): string {
  return run.replay_of
    ? `Injecting mail is not available on a replay run (${run.status}).`
    : `Injecting mail needs a RUNNING run; this run is ${run.status}.`;
}
