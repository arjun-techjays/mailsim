import { diffRows } from './diff';

describe('diffRows', () => {
  it('marks keys missing on one side as absent and differing', () => {
    expect(diffRows({ expected: { a: 1, b: 'x' }, actual: { b: 'x', c: null }, explanation: '' })).toEqual([
      { key: 'a', expected: '1', actual: '(absent)', same: false },
      { key: 'b', expected: '"x"', actual: '"x"', same: true },
      { key: 'c', expected: '(absent)', actual: 'null', same: false },
    ]);
  });

  it('handles missing expected/actual objects', () => {
    expect(diffRows({ explanation: '' })).toEqual([]);
  });
});
