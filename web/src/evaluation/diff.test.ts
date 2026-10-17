import { diffRows, inRange } from './diff';

describe('diffRows', () => {
  it('labels one-sided fields as criterion/observation, never as differences', () => {
    expect(diffRows({ expected: { a: 1, b: 'x' }, actual: { b: 'x', c: null }, explanation: '' })).toEqual([
      { key: 'a', expected: '1', actual: '(absent)', comparison: 'expected-only', label: 'criterion only', flagged: false },
      { key: 'b', expected: '"x"', actual: '"x"', comparison: 'same', label: 'same', flagged: false },
      { key: 'c', expected: '(absent)', actual: 'null', comparison: 'observed-only', label: 'observed only', flagged: false },
    ]);
  });

  it('a PASS (matched) assertion never says "differs" (UI-3)', () => {
    const rows = diffRows({
      expected: { contains: ['verif'], count: { min: 1 }, regex: null, to: 'a@x.test' },
      actual: { count: 1, message_ids: ['msg_1'], to: 'b@x.test' },
      matched: true,
      explanation: '',
    });
    expect(rows.map((r) => [r.key, r.label, r.flagged])).toEqual([
      ['contains', 'criterion only', false],
      ['count', 'within range', false],
      ['message_ids', 'observed only', false],
      ['regex', 'criterion only', false],
      ['to', 'not equal (not a failure)', false],
    ]);
    expect(rows.some((r) => r.label === 'differs')).toBe(false);
  });

  it('flags what made a failed assertion fail', () => {
    const rows = diffRows({ expected: { applied: false, count: { min: 2, max: null } }, actual: { applied: true, count: 1 }, matched: false, explanation: '' });
    expect(rows.map((r) => [r.key, r.label, r.flagged])).toEqual([
      ['applied', 'differs', true],
      ['count', 'outside range', true],
    ]);
  });

  it('checks ranges only for {min,max,eq,exactly} against numbers', () => {
    expect(inRange({ min: 1, max: 3 }, 2)).toBe(true);
    expect(inRange({ eq: 2 }, 3)).toBe(false);
    expect(inRange({ exactly: 3 }, 3)).toBe(true);
    expect(inRange({ max: 1 }, 2)).toBe(false);
    expect(inRange({ min: 1 }, '1')).toBeNull();
    expect(inRange([1], 1)).toBeNull();
    expect(inRange(null, 1)).toBeNull();
    expect(inRange({}, 1)).toBeNull();
    expect(inRange({ min: 'x' }, 1)).toBeNull();
    expect(inRange({ other: 1 }, 1)).toBeNull();
  });

  it('handles missing expected/actual objects', () => {
    expect(diffRows({ explanation: '' })).toEqual([]);
  });
});
