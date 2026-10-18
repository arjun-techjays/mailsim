import { AUTHORING_META } from '../mocks/authoring';
import { clearAllDrafts, clearDraft, draftKey, loadDraft, saveDraft } from './draftStorage';
import { labelsOf, personaOptions } from './useScenarioContext';
import {
  checksOff,
  defaultCheck,
  emptyDraft,
  fieldId,
  fromDocument,
  getIn,
  newAssertion,
  pruneCheck,
  setIn,
  toDocument,
  uniqueId,
} from './model';
import { itemsOf, kindOf, labelFor, properties, pruneEmptyOptional, refName, requiredKeys, resolve, unwrap } from './schema';
import { byPath, clientIssues, fromServer, isSourcePath, leakedFields, mergeIssues, normalizePath, yamlProblems } from './validation';

const meta = AUTHORING_META;

describe('answer-key model', () => {
  it('builds defaults for every type', () => {
    for (const t of [...meta.assertion_types.map((x) => x.type), 'mystery']) {
      expect(defaultCheck(t, meta)).toBeTypeOf('object');
    }
    expect(defaultCheck('action', { ...meta, agent_operations: ['read'] })).toEqual({ operation: 'read' });
    expect(defaultCheck('action', { ...meta, agent_operations: [] })).toEqual({ operation: '' });
    expect(defaultCheck('safety', { ...meta, invariants: [] })).toEqual({ invariant: '' });
    const sem = newAssertion('semantic', meta, []);
    expect(sem).toMatchObject({ id: 'semantic-check', useCheck: false, check: {} });
    expect(newAssertion('state', meta, ['state-check', 'state-check-2']).id).toBe('state-check-3');
    expect(newAssertion('mystery', meta, []).useCheck).toBe(false);
    expect(uniqueId('x', [])).toBe('x');
  });

  it('round-trips a document through the form', () => {
    const doc = {
      answer_key_version: '1.0',
      answer_key: {
        scenario: 's',
        version: 4,
        scenario_versions: [2, 1],
        checks_default: 'off',
        assertions: [
          {
            id: 'a',
            type: 'state',
            requirement: 'r',
            severity: 'advisory',
            category: 'safety',
            subject: 'simulator',
            evaluate_at: { at: 'after_event', event: 'e1' },
            check: { where: { folder: 'sent', label: '' }, count: { min: 1 } },
            rubric: 'x',
          },
          { id: 'b', type: 'semantic', requirement: 'q' },
          'junk',
        ],
      },
    };
    const d = fromDocument(doc, 'fallback');
    expect(d).toMatchObject({ scenario: 's', version: '4', allVersions: false, scenarioVersions: [2, 1], checksDefault: 'off' });
    expect(d.assertions[0]).toMatchObject({ subject: 'simulator', evaluateAt: 'after_event', evaluateEvent: 'e1', useCheck: true });
    expect(d.assertions[2]).toMatchObject({ id: '', type: '', subject: 'agent', evaluateAt: 'end', useCheck: false });
    const back = toDocument(d) as { answer_key: Record<string, unknown> };
    expect(back.answer_key.scenario_versions).toEqual([1, 2]);
    expect(back.answer_key.version).toBe(4);
    const [a, b] = back.answer_key.assertions as Record<string, unknown>[];
    expect(a).toEqual({
      id: 'a',
      type: 'state',
      requirement: 'r',
      severity: 'advisory',
      category: 'safety',
      subject: 'simulator',
      evaluate_at: { at: 'after_event', event: 'e1' },
      check: { where: { folder: 'sent' }, count: { min: 1 } },
      rubric: 'x',
    });
    expect(b).toEqual({ id: 'b', type: 'semantic', requirement: 'q' });
    // checks_default as the server reads it: false/no (any case) mean off too
    expect([false, 'off', 'False', ' NO ', 'false'].every(checksOff)).toBe(true);
    expect([true, 'on', 'yes', 'TRUE', undefined, 0].some(checksOff)).toBe(false);
    expect(fromDocument({ answer_key: { checks_default: false } }, 'k').checksDefault).toBe('off');
    const empty = fromDocument({}, 'k');
    expect(empty).toMatchObject({ scenario: 'k', version: '', allVersions: true, checksDefault: 'on', assertions: [] });
    expect((toDocument({ ...emptyDraft('k', 1), version: 'x' }) as { answer_key: { version: unknown } }).answer_key.version).toBe('x');
  });

  it('prunes and updates nested values immutably', () => {
    expect(pruneCheck({ a: '', b: undefined, c: [{ d: '' }, 1], e: { f: 0 } })).toEqual({ c: [{}, 1], e: { f: 0 } });
    const root = { a: { b: [1, 2] } };
    const next = setIn(root, ['a', 'b', 1], 5);
    expect(next).toEqual({ a: { b: [1, 5] } });
    expect(root.a.b[1]).toBe(2);
    expect(setIn(root, ['a', 'b'], undefined)).toEqual({ a: {} });
    expect(setIn(undefined, ['x'], 1)).toEqual({ x: 1 });
    expect(getIn(root, ['a', 'b', 0])).toBe(1);
    expect(getIn(root, ['a', 'z', 'q'])).toBeUndefined();
    expect(fieldId(['assertions', 0, 'check'])).toBe('ak-assertions-0-check');
    expect(fieldId('a.b c')).toBe('ak-a-b-c');
  });
});

describe('JSON schema helpers', () => {
  const root = { $defs: { Count: { type: 'object', properties: { eq: { type: 'integer' } } } } };
  it('resolves refs, allOf and optional anyOf', () => {
    expect(refName({ $ref: '#/$defs/Count' })).toBe('Count');
    expect(refName({ allOf: [{ $ref: '#/$defs/Count' }] })).toBe('Count');
    expect(refName({ type: 'string' })).toBeUndefined();
    expect(resolve({ $ref: '#/$defs/Count', description: 'd' }, root)).toMatchObject({ type: 'object', description: 'd' });
    expect(resolve({ $ref: '#/$defs/Nope' }, {})).toEqual({});
    expect(resolve({ allOf: [{ $ref: '#/$defs/Count' }] }, root)).toMatchObject({ type: 'object' });
    expect(unwrap({ anyOf: [{ $ref: '#/$defs/Count' }, { type: 'null' }], default: null }, root)).toMatchObject({
      optional: true,
      name: 'Count',
      schema: { type: 'object', default: null },
    });
    expect(unwrap({ anyOf: [{ type: 'string' }, { type: 'integer' }] }, root).optional).toBe(false);
  });
  it('classifies kinds and reads properties', () => {
    expect(kindOf({ enum: ['a'] })).toBe('enum');
    expect(kindOf({ type: 'object' })).toBe('map');
    expect(kindOf({ properties: {} })).toBe('object');
    expect(kindOf({ type: 'array' })).toBe('array');
    expect(kindOf({ type: 'boolean' })).toBe('boolean');
    expect(kindOf({})).toBe('unknown');
    expect(properties({ properties: { a: {}, b: 3 } })).toEqual([['a', {}]]);
    expect(properties({})).toEqual([]);
    expect(requiredKeys({ required: ['a', 1] })).toEqual(['a']);
    expect(requiredKeys({})).toEqual([]);
    expect(itemsOf({ items: { $ref: '#/$defs/Count' } }, root)).toMatchObject({ type: 'object' });
    expect(itemsOf({}, root)).toEqual({});
    expect(labelFor('has_flag', { title: 'Has Flag' })).toBe('Has flag');
    expect(labelFor('in_reply_to', {})).toBe('In reply to');
  });
});

describe('validation mapping', () => {
  it('normalises server paths and leak findings', () => {
    expect(normalizePath('answer_key.assertions.0.check')).toBe('assertions.0.check');
    expect(normalizePath('body.answer_key')).toBe('');
    expect(normalizePath('document.answer_key.assertions[2].id')).toBe('assertions.2.id');
    const issues = fromServer([
      { path: 'leak:3', message: 'x "secret text" y' },
      { path: 'answer_key.version', message: 'bad' },
    ]);
    expect(issues).toEqual([
      { path: '', message: 'x "secret text" y', leakVersion: '3' },
      { path: 'version', message: 'bad' },
    ]);
    const draft = emptyDraft('s', 1);
    draft.assertions = [
      { ...newAssertion('state', meta, []), requirement: 'secret text', rubric: 'short', id: 'secret text' },
    ];
    expect(leakedFields(draft.assertions, issues.filter((i) => i.leakVersion)).map((i) => i.path)).toEqual([
      'assertions.0.requirement',
      'assertions.0.id',
    ]);
    const m = byPath([...issues, { path: 'version', message: 'again' }]);
    expect(m.get('version')).toEqual(['bad', 'again']);
    expect(m.has('')).toBe(false);
  });

  it('client checks and merge', () => {
    const d = { ...emptyDraft('s', 1), version: '0', allVersions: false };
    d.assertions = [
      { ...newAssertion('state', meta, []), id: '' },
      { ...newAssertion('state', meta, []), id: 'x', requirement: 'r' },
      { ...newAssertion('state', meta, []), id: 'x', requirement: 'r' },
    ];
    const issues = clientIssues(d);
    expect(issues.map((i) => i.path)).toEqual([
      'version',
      'scenario_versions',
      'assertions.0.id',
      'assertions.0.requirement',
      'assertions.2.id',
    ]);
    expect(issues[4]?.message).toContain('assertion 2');
    expect(mergeIssues([{ path: 'a', message: 'm' }], [{ path: 'a', message: 'm' }, { path: 'b', message: 'n' }])).toHaveLength(2);
    // UI-1: a YAML parse error is one problem, however many endpoints report it.
    expect(['', '<source>', '<root>', 'line 3'].every(isSourcePath)).toBe(true);
    expect(isSourcePath('assertions.0')).toBe(false);
    const parse = { path: 'line 2', message: 'bad' };
    const server = [{ path: '<source>', message: 'bad' }, { path: '', message: 'not a mapping' }, { path: 'version', message: 'v' }];
    expect(yamlProblems(parse, server)).toEqual([parse, { path: 'version', message: 'v' }]);
    expect(yamlProblems(null, server)).toBe(server);
  });
});

describe('draft storage', () => {
  it('keeps the draft of this tab only and tolerates broken storage', () => {
    const key = draftKey('s', null);
    expect(key).toBe('mailsim.answerKeyDraft:s:new');
    expect(draftKey('s', 2)).toBe('mailsim.answerKeyDraft:s:2');
    expect(loadDraft(key)).toBeNull();
    saveDraft(key, emptyDraft('s', 1));
    expect(loadDraft(key)).toMatchObject({ scenario: 's', version: '1' });
    expect(localStorage.length).toBe(0);
    sessionStorage.setItem(key, '{"x":1}');
    expect(loadDraft(key)).toBeNull();
    sessionStorage.setItem(key, 'not json');
    expect(loadDraft(key)).toBeNull();
    clearDraft(key);
    expect(sessionStorage.getItem(key)).toBeNull();
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('full');
    });
    expect(() => saveDraft(key, emptyDraft('s', 1))).not.toThrow();
    spy.mockRestore();
    const rm = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(() => clearDraft(key)).not.toThrow();
    rm.mockRestore();
  });
});

describe('review helpers', () => {
  it('clears all drafts and survives a broken storage', () => {
    sessionStorage.setItem(draftKey('a', null), '{}');
    sessionStorage.setItem('other', '1');
    clearAllDrafts();
    expect(Object.keys(sessionStorage)).toEqual(['other']);
    const spy = vi.spyOn(window, 'sessionStorage', 'get').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(() => clearAllDrafts()).not.toThrow();
    spy.mockRestore();
  });

  it('collects labels and persona options defensively', () => {
    expect(labelsOf([{ labels: ['B', 'A', 3] }, { labels: 'x' }, { labels: ['A'] }, null])).toEqual(['A', 'B']);
    expect(labelsOf(undefined)).toEqual([]);
    expect(personaOptions([{ id: 'p', email: 'e' }, { id: '' }, { id: 'q', name: 'Q', role: 'R' }])).toEqual([
      { id: 'p', email: 'e', label: 'p' },
      { id: 'q', email: '', label: 'Q, R' },
    ]);
  });
});

describe('pruneEmptyOptional', () => {
  const schemaOf = (t: string) => AUTHORING_META.assertion_types.find((x) => x.type === t)?.check_schema as Record<string, unknown>;
  it('drops empty optional groups but keeps explicit empty required/defaulted groups', () => {
    expect(pruneEmptyOptional({ forbid: { operation: 'send' }, unless: {} }, schemaOf('policy'))).toEqual({ forbid: { operation: 'send' } });
    expect(pruneEmptyOptional({ forbid: { operation: 'send' }, unless: { where: {} } }, schemaOf('policy'))).toEqual({
      forbid: { operation: 'send' },
      unless: { where: {} },
    });
    expect(pruneEmptyOptional({ where: {}, contains: ['x'], extra: 1 }, schemaOf('content'))).toEqual({ where: {}, contains: ['x'], extra: 1 });
    expect(pruneEmptyOptional('x', schemaOf('content'))).toBe('x');
    const d = { ...emptyDraft('s', 1) };
    d.assertions = [{ ...newAssertion('policy', AUTHORING_META, []), check: { forbid: { operation: 'send' }, unless: {} } }];
    const body = (toDocument(d, AUTHORING_META) as { answer_key: { assertions: { check: unknown }[] } }).answer_key;
    expect(body.assertions[0]?.check).toEqual({ forbid: { operation: 'send' } });
    d.assertions = [{ ...newAssertion('mystery', AUTHORING_META, []), useCheck: true, check: { a: {} } }];
    expect((toDocument(d, AUTHORING_META) as { answer_key: { assertions: { check: unknown }[] } }).answer_key.assertions[0]?.check).toEqual({ a: {} });
  });
});
