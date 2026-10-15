import type { AssertionDiff } from '../api/types';

const show = (v: unknown) => (v === undefined ? '(absent)' : JSON.stringify(v));

export function diffRows(diff: AssertionDiff) {
  const expected = diff.expected ?? {};
  const actual = diff.actual ?? {};
  const keys = [...new Set([...Object.keys(expected), ...Object.keys(actual)])].sort();
  return keys.map((k) => {
    const e = show(expected[k]);
    const a = show(actual[k]);
    return { key: k, expected: e, actual: a, same: e === a };
  });
}
