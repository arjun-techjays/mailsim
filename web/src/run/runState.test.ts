import type { RunOut } from '../api/types';
import { canDo, isReplayRun, isTerminal, replayOf, stateHint } from './runState';

const run = (extra: Partial<RunOut> & Record<string, unknown> = {}): RunOut =>
  ({ id: 'r', status: 'RUNNING', epoch: 1, virtual_time: null, agent_actions: 0, replay_of: null, ...extra }) as RunOut;

describe('run state rules (UI-6)', () => {
  it('offers only actions the state allows', () => {
    expect(['start', 'cancel', 'reset', 'replay', 'clock', 'inject'].filter((a) => canDo('READY', a as never))).toEqual([
      'start',
      'cancel',
      'reset',
      'replay',
    ]);
    expect(['start', 'cancel', 'reset', 'replay', 'clock', 'inject'].filter((a) => canDo('RUNNING', a as never))).toEqual([
      'cancel',
      'reset',
      'replay',
      'clock',
      'inject',
    ]);
    for (const s of ['PASSED', 'FAILED', 'ERROR', 'CANCELLED']) {
      expect(isTerminal(s)).toBe(true);
      expect(['start', 'cancel', 'reset', 'replay', 'clock', 'inject'].filter((a) => canDo(s, a as never))).toEqual(['replay']);
      expect(stateHint(s)).toContain('only replay (and evaluation) remain');
    }
    expect(isTerminal('READY')).toBe(false);
    expect(isTerminal('REPLAYED')).toBe(true);
    expect(canDo('REPLAYED', 'start')).toBe(false);
    expect(stateHint('REPLAYED')).toContain('This run is a replay (REPLAYED)');
  });

  it('detects replays from replay_of or kind, and handles their absence', () => {
    expect(replayOf(run({ replay_of: 'run_1' }))).toBe('run_1');
    expect(replayOf(run({ kind: 'replay' }))).toBeNull();
    expect(replayOf(run({ kind: 'normal' }))).toBe(false);
    expect(replayOf(run())).toBe(false);
    expect(isReplayRun(run({ replay_of: 'x' }))).toBe(true);
    expect(isReplayRun(run())).toBe(false);
  });
});
