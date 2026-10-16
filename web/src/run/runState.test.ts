import { canDo, injectOffReason, isTerminal, stateHint, type RunAction } from './runState';

const ACTIONS: RunAction[] = ['start', 'cancel', 'reset', 'replay', 'clock', 'inject'];
const run = (status: string, replay_of: string | null = null) => ({ status, replay_of });
const allowed = (status: string, replay_of: string | null = null) => ACTIONS.filter((a) => canDo(run(status, replay_of), a));

describe('run state rules (UI-6)', () => {
  it('offers only actions the state allows', () => {
    expect(allowed('READY')).toEqual(['start', 'cancel', 'reset', 'replay']);
    expect(allowed('RUNNING')).toEqual(['cancel', 'reset', 'replay', 'clock', 'inject']);
    for (const s of ['PASSED', 'FAILED', 'ERROR', 'CANCELLED']) {
      expect(isTerminal(s)).toBe(true);
      expect(allowed(s)).toEqual(['replay']);
      expect(stateHint(run(s))).toContain('only replay (and evaluation) remain');
    }
    expect(isTerminal('READY')).toBe(false);
    expect(isTerminal('REPLAYED')).toBe(true);
    expect(allowed('REPLAYED')).toEqual(['replay']);
    expect(stateHint(run('REPLAYED'))).toContain('This run is a replay (REPLAYED)');
  });

  it('a replay run only offers replay, whatever status a legacy replay was stored with', () => {
    expect(allowed('RUNNING', 'run_o')).toEqual(['replay']);
    expect(allowed('READY', 'run_o')).toEqual(['replay']);
    expect(stateHint(run('RUNNING', 'run_o'))).toContain('This run is a replay (RUNNING)');
    expect(injectOffReason(run('RUNNING', 'run_o'))).toBe('Injecting mail is not available on a replay run (RUNNING).');
    expect(injectOffReason(run('CANCELLED'))).toBe('Injecting mail needs a RUNNING run; this run is CANCELLED.');
  });
});
