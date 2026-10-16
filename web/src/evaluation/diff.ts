import type { AssertionDiff } from '../api/types';

const show = (v: unknown) => (v === undefined ? '(absent)' : JSON.stringify(v));

/**
 * How one field of an assertion's expected (answer-key criterion) and actual (what the run
 * recorded) relate. The two sides are different shapes (a criterion such as `count: {min: 1}`
 * versus an observation such as `count: 1`), so a field only present on one side is not a
 * difference, and a range criterion is checked against the observed number.
 */
export type Comparison = 'same' | 'within' | 'outside' | 'different' | 'expected-only' | 'observed-only';

export interface DiffRow {
  key: string;
  expected: string;
  actual: string;
  comparison: Comparison;
  /** Text for the Comparison column. */
  label: string;
  /** Whether the row points at why the assertion did not match. */
  flagged: boolean;
}

const RANGE_KEYS = new Set(['min', 'max', 'eq', 'exactly']);

/** `{min, max, eq|exactly}` against a number: true/false, or null when it is not such a pair. */
export function inRange(expected: unknown, actual: unknown): boolean | null {
  if (typeof actual !== 'number' || !expected || typeof expected !== 'object' || Array.isArray(expected)) return null;
  const entries = Object.entries(expected as Record<string, unknown>);
  if (entries.length === 0 || !entries.every(([k, v]) => RANGE_KEYS.has(k) && (v === null || typeof v === 'number'))) return null;
  return entries.every(([k, v]) => {
    if (v === null) return true;
    const n = v as number;
    return k === 'min' ? actual >= n : k === 'max' ? actual <= n : actual === n;
  });
}

function compare(e: unknown, a: unknown): Comparison {
  if (e === undefined) return 'observed-only';
  if (a === undefined) return 'expected-only';
  if (JSON.stringify(e) === JSON.stringify(a)) return 'same';
  const range = inRange(e, a);
  if (range !== null) return range ? 'within' : 'outside';
  return 'different';
}

export function diffRows(diff: AssertionDiff): DiffRow[] {
  const expected = diff.expected ?? {};
  const actual = diff.actual ?? {};
  const failed = diff.matched === false;
  const keys = [...new Set([...Object.keys(expected), ...Object.keys(actual)])].sort();
  return keys.map((k) => {
    const comparison = compare(expected[k], actual[k]);
    const label = {
      same: 'same',
      within: 'within range',
      outside: 'outside range',
      // On a matched (PASS) assertion an unequal field is not a failure: never call it "differs".
      different: failed ? 'differs' : 'not equal (not a failure)',
      'expected-only': 'criterion only',
      'observed-only': 'observed only',
    }[comparison];
    return {
      key: k,
      expected: show(expected[k]),
      actual: show(actual[k]),
      comparison,
      label,
      flagged: comparison === 'outside' || (comparison === 'different' && failed),
    };
  });
}
