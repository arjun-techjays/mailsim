// Form model of an answer key (src/mailsim/scenario/answer_key.py: AnswerKeyDoc) and the
// conversions between the form and the stored document.
import type { AuthoringMeta } from '../api/types';

export type Check = Record<string, unknown>;

export interface AssertionDraft {
  /** Stable React key; never sent to the API. */
  key: string;
  id: string;
  type: string;
  /** '' = the type's default (semantic: advisory, others: mandatory). */
  severity: string;
  /** '' = the type's default category. */
  category: string;
  requirement: string;
  rubric: string;
  subject: 'agent' | 'simulator';
  evaluateAt: 'end' | 'after_event';
  evaluateEvent: string;
  /** Deterministic `check:` block in use (never for semantic assertions). */
  useCheck: boolean;
  check: Check;
}

export interface KeyDraft {
  scenario: string;
  /** Kept as text so the field can be edited freely; the server validates it. */
  version: string;
  /** true = compatible with every scenario version (scenario_versions: null). */
  allVersions: boolean;
  scenarioVersions: number[];
  checksDefault: 'on' | 'off';
  assertions: AssertionDraft[];
}

/** One line per assertion type, shown next to the type picker. */
export const TYPE_HELP: Record<string, string> = {
  state: 'The mailbox ends up in a given state: messages matching filters exist (and how many).',
  action: 'The agent performed an operation (for example reply) with a given outcome.',
  content: 'Matching messages contain (or do not contain) phrases or a regular expression.',
  policy: 'A forbidden action never happened, unless a state condition held beforehand.',
  safety: 'A built-in safety invariant held for the whole run.',
  invariant: 'A built-in safety invariant held for the whole run (same as safety).',
  temporal: 'One thing happened before another, optionally within a time window.',
  protocol: 'The agent made at most N invalid or unsupported API calls.',
  semantic: 'Judged by an LLM against your rubric. There is no deterministic check.',
};

export const DEFAULT_SEVERITY = (type: string) => (type === 'semantic' ? 'advisory' : 'mandatory');

let seq = 0;
export const newKey = () => `a${(seq += 1)}`;

/** A valid starting check per type (`events`: scenario event ids, used to seed temporal checks). */
export function defaultCheck(type: string, meta: AuthoringMeta, events: string[] = []): Check {
  switch (type) {
    case 'state':
      return { where: { folder: 'sent' }, count: { min: 1 } };
    case 'action':
      return { operation: meta.agent_operations.includes('reply') ? 'reply' : (meta.agent_operations[0] ?? '') };
    case 'content':
      return { where: { folder: 'sent' }, contains: [] };
    case 'policy':
      return { forbid: { operation: 'send' } };
    case 'safety':
    case 'invariant':
      return { invariant: meta.invariants[0] ?? '' };
    case 'temporal':
      return {
        first: events[0] ? { event: events[0] } : { action: { operation: 'read' } },
        then: { action: { operation: 'reply' } },
      };
    case 'protocol':
      return { max_errors: 0 };
    default:
      return {};
  }
}

export function uniqueId(base: string, taken: string[]): string {
  if (!taken.includes(base)) return base;
  let n = 2;
  while (taken.includes(`${base}-${n}`)) n += 1;
  return `${base}-${n}`;
}

export function newAssertion(type: string, meta: AuthoringMeta, taken: string[], events: string[] = []): AssertionDraft {
  return {
    key: newKey(),
    id: uniqueId(`${type}-check`, taken),
    type,
    severity: '',
    category: '',
    requirement: '',
    rubric: '',
    subject: 'agent',
    evaluateAt: 'end',
    evaluateEvent: '',
    useCheck: type !== 'semantic' && meta.assertion_types.some((t) => t.type === type && t.check_schema != null),
    check: type === 'semantic' ? {} : defaultCheck(type, meta, events),
  };
}

export function emptyDraft(scenario: string, version: number): KeyDraft {
  return { scenario, version: String(version), allVersions: true, scenarioVersions: [], checksDefault: 'on', assertions: [] };
}

/** Drops empty optional values so server defaults apply ("" / undefined / empty objects). */
export function pruneCheck(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(pruneCheck);
  if (v && typeof v === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, val] of Object.entries(v)) {
      if (val === undefined || val === '') continue;
      out[k] = pruneCheck(val);
    }
    return out;
  }
  return v;
}

export function toDocument(d: KeyDraft): Record<string, unknown> {
  const version = /^\d+$/.test(d.version.trim()) ? Number(d.version) : d.version;
  return {
    answer_key_version: '1.0',
    answer_key: {
      scenario: d.scenario,
      version,
      scenario_versions: d.allVersions ? null : [...d.scenarioVersions].sort((a, b) => a - b),
      checks_default: d.checksDefault,
      assertions: d.assertions.map((a) => {
        const out: Record<string, unknown> = { id: a.id, type: a.type, requirement: a.requirement };
        if (a.severity) out.severity = a.severity;
        if (a.category) out.category = a.category;
        if (a.subject !== 'agent') out.subject = a.subject;
        if (a.evaluateAt === 'after_event') out.evaluate_at = { at: 'after_event', event: a.evaluateEvent };
        if (a.type !== 'semantic' && a.useCheck) out.check = pruneCheck(a.check);
        if (a.rubric.trim()) out.rubric = a.rubric;
        return out;
      }),
    },
  };
}

const str = (v: unknown, d = '') => (typeof v === 'string' ? v : d);
const obj = (v: unknown): Record<string, unknown> =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

/** Reads a stored/parsed document into the form (unknown keys are dropped: the model is strict). */
export function fromDocument(doc: Record<string, unknown>, scenario: string): KeyDraft {
  const body = obj(doc.answer_key);
  const versions = Array.isArray(body.scenario_versions)
    ? body.scenario_versions.filter((v): v is number => typeof v === 'number')
    : null;
  const assertions = Array.isArray(body.assertions) ? body.assertions : [];
  return {
    scenario: str(body.scenario, scenario),
    version: body.version == null ? '' : String(body.version),
    allVersions: versions === null,
    scenarioVersions: versions ?? [],
    checksDefault: body.checks_default === 'off' ? 'off' : 'on',
    assertions: assertions.map((raw) => {
      const a = obj(raw);
      const at = obj(a.evaluate_at);
      const type = str(a.type);
      return {
        key: newKey(),
        id: str(a.id),
        type,
        severity: str(a.severity),
        category: str(a.category),
        requirement: str(a.requirement),
        rubric: str(a.rubric),
        subject: a.subject === 'simulator' ? 'simulator' : 'agent',
        evaluateAt: at.at === 'after_event' ? 'after_event' : 'end',
        evaluateEvent: str(at.event),
        useCheck: a.check != null && typeof a.check === 'object',
        check: obj(a.check),
      };
    }),
  };
}

const TOP_KEYS = ['answer_key_version', 'answer_key'];
const BODY_KEYS = ['scenario', 'version', 'scenario_versions', 'checks_default', 'assertions'];
const ASSERTION_KEYS = ['id', 'type', 'requirement', 'severity', 'category', 'subject', 'evaluate_at', 'check', 'rubric'];

/** Keys of a parsed document that the form (and the strict server model) does not know. */
export function droppedKeys(doc: Record<string, unknown>): string[] {
  const out = Object.keys(doc).filter((k) => !TOP_KEYS.includes(k));
  const body = obj(doc.answer_key);
  out.push(...Object.keys(body).filter((k) => !BODY_KEYS.includes(k)).map((k) => `answer_key.${k}`));
  (Array.isArray(body.assertions) ? body.assertions : []).forEach((a, i) => {
    out.push(...Object.keys(obj(a)).filter((k) => !ASSERTION_KEYS.includes(k)).map((k) => `answer_key.assertions.${i}.${k}`));
  });
  return out;
}

// ---- immutable path updates (paths are arrays of keys / indexes) ---------------------------
export type Path = (string | number)[];

export function getIn(v: unknown, path: Path): unknown {
  let cur = v;
  for (const p of path) {
    if (cur == null || typeof cur !== 'object') return undefined;
    cur = (cur as Record<string | number, unknown>)[p];
  }
  return cur;
}

/** Sets (or, with `undefined`, removes) the value at `path`, copying along the way. */
export function setIn<T>(root: T, path: Path, value: unknown): T {
  if (path.length === 0) return value as T;
  const [head, ...rest] = path as [string | number, ...Path];
  const base: Record<string | number, unknown> | unknown[] = Array.isArray(root)
    ? [...root]
    : { ...((root ?? {}) as Record<string, unknown>) };
  const next = setIn((base as Record<string | number, unknown>)[head], rest, value);
  if (next === undefined && !Array.isArray(base)) delete (base as Record<string | number, unknown>)[head];
  else (base as Record<string | number, unknown>)[head] = next;
  return base as T;
}

/** "assertions.0.check.where.folder" for a field path. */
export const pathKey = (path: Path) => path.join('.');
/** DOM id of the control editing `path`. */
export const fieldId = (path: Path | string) => `ak-${(typeof path === 'string' ? path : pathKey(path)).replace(/[^\w-]/g, '-')}`;
