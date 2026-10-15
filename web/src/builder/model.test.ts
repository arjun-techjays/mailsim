import { dumpYaml, parseYaml } from '../mocks/builder';
import {
  blankEvent,
  blankForm,
  blankPersona,
  defaultEmail,
  defaultStartAt,
  documentKey,
  fromDocument,
  num,
  personaLabel,
  personaRemovalEffects,
  personasNeedingMailbox,
  refLabel,
  referencedFixtures,
  removePersona,
  setPersonaId,
  slugId,
  toDocument,
  uid,
  uniqueId,
  unknownKeys,
  usesV11,
  type ScenarioForm,
} from './model';
import { TEMPLATE_DOCUMENT, templateDraft } from './template';

/** A canonical document using every part the form edits. */
const FULL = {
  schema_version: '1.1',
  scenario: {
    id: 'x.full',
    version: 1,
    title: 'Full',
    tags: ['a'],
    environment: { seed: 1, clock: { start_at: '2026-10-15T09:00:00Z' }, delivery: { external_delivery: 'deny', persona_replies: 'disabled' } },
    organization: { domain: 'acme.test', name: 'Acme', policies: [{ id: 'p1', rule: 'Be careful' }] },
    personas: [
      { id: 'alice', name: 'Alice', email: 'alice@acme.test', role: 'Finance', knowledge: ['k1'], behavior: {} },
      {
        id: 'bob',
        email: 'bob@acme.test',
        role: 'Vendor',
        knowledge: [],
        behavior: {
          mode: 'scripted',
          response_style: 'terse',
          response_delay_seconds: 30,
          script: [
            { on: { from: 'alice', subject_contains: 'INV', body_contains: 'pay' }, reply: { subject: 'Re: INV', body: 'ok' }, delay_seconds: 5, once: false },
            { on: {}, reply: { body: '' }, once: true },
          ],
        },
      },
    ],
    relationships: [{ source: 'alice', target: 'bob', type: 'vendor' }, 'junk'],
    projects: [{ id: 'p', name: 'P', participants: ['alice', 'bob', 7] }],
    mailboxes: [
      { owner: 'alice', initial_messages: [{ fixture: 'fixtures/a.eml' }] },
      { owner: 'bob', initial_messages: [] },
    ],
    events: [
      { id: 'event-1', trigger: { type: 'after_seconds', seconds: 600 }, event: { type: 'email.receive', from: 'bob', to: 'alice', subject: '', body: '' } },
      {
        id: 'event-2',
        trigger: { type: 'at', at: '2026-10-15T10:00:00Z', delay_seconds: 7 },
        event: { type: 'email.receive', from: 'bob', to: ['alice', 'bob'], subject: '', body: '' },
      },
      { id: 'event-3', trigger: { type: 'after_agent_action', action: 'send_reply' }, event: { type: 'email.receive', from: 'bob', to: [], subject: '', body: '' } },
    ],
    agent: { mailbox: 'alice', allowed_operations: ['read', 'search', 'reply'], prohibited_operations: ['external_delivery'] },
    evaluation: { limits: { max_agent_actions: 100, max_virtual_duration_seconds: 86400 } },
  },
};

type Doc = {
  mailboxes: { owner: string }[];
  agent: { mailbox: string };
  events: { event: { to: unknown } }[];
  personas: { behavior: { script?: { on: { from?: string } }[] } }[];
  relationships: unknown[];
  projects: { participants: unknown[] }[];
};
const fullForm = (): ScenarioForm => fromDocument(FULL).form;
const scenarioOf = (f: ScenarioForm) => toDocument(f).scenario as unknown as Doc;

describe('model helpers', () => {
  it('slugs, defaults and unique ids', () => {
    expect(slugId('  Fïnance Manager! ')).toBe('finance_manager');
    expect(slugId('***')).toBe('');
    expect(defaultEmail('a', 'x.test')).toBe('a@x.test');
    expect(defaultEmail('', 'x.test')).toBe('');
    expect(defaultStartAt(new Date('2026-10-15T09:41:12Z'))).toBe('2026-10-15T09:00:00Z');
    expect(defaultStartAt()).toMatch(/^\d{4}-\d\d-\d\dT\d\d:00:00Z$/);
    expect(uniqueId('p', [])).toBe('p');
    expect(uniqueId('p', ['p', 'p_2'])).toBe('p_3');
    expect(uniqueId('p', ['p'], '-')).toBe('p-2');
    expect(uid()).not.toBe(uid());
    expect(uid().startsWith('#')).toBe(true);
    expect(blankPersona().email).toBe('');
    expect(blankPersona(['persona'], 'd.test')).toMatchObject({ id: 'persona_2', email: 'persona_2@d.test' });
    expect(blankEvent(['event-1', 'event-2']).id).toBe('event-3');
    expect(num(' 12 ')).toBe(12);
    expect(num('-3')).toBe(-3);
    expect(num('1.5')).toBe('1.5');
    expect(num('')).toBe('');
    expect(personaLabel({ id: 'a', name: ' ', email: '' })).toBe('a');
    expect(personaLabel({ id: '', name: '', email: 'x@y' })).toBe('(no id) <x@y>');
    expect(personaLabel({ id: 'a', name: 'Al', email: 'a@y' })).toBe('Al <a@y>');
  });

  it('writes the canonical document: references resolved to ids, optional parts omitted, no assertions', () => {
    const f = fullForm();
    expect(f.mailboxes[0]?.owner).toBe(f.personas[0]?.uid);
    expect(f.events[1]?.to).toEqual([f.personas[0]?.uid, f.personas[1]?.uid]);
    expect(toDocument(f)).toEqual(FULL);
    expect(Object.keys(toDocument(f).scenario as object)).toEqual([
      'id', 'version', 'title', 'tags', 'environment', 'organization', 'personas', 'relationships', 'projects',
      'mailboxes', 'events', 'agent', 'evaluation',
    ]);
    const plain = blankForm();
    expect(toDocument(plain).scenario).not.toHaveProperty('relationships');
    expect((toDocument(plain).scenario as { organization: unknown }).organization).toEqual({ domain: '', policies: [] });
  });

  it('writes schema 1.1 only when 1.1 features are used', () => {
    const legacy = fromDocument({ schema_version: '1.0', scenario: { personas: [{ id: 'a', email: 'a@x.test' }] } }).form;
    expect(usesV11(legacy)).toBe(false);
    expect(toDocument(legacy).schema_version).toBe('1.0');
    const changes: ((f: ScenarioForm) => void)[] = [
      (f) => void (f.personas[0]!.name = 'A'),
      (f) => void (f.personas[0]!.mode = 'llm'),
      (f) => void f.personas[0]!.script.push({ uid: 'r', from: '', subject_contains: '', body_contains: '', reply_subject: '', reply_body: 'x', delay_seconds: '', once: true }),
    ];
    for (const change of changes) {
      const f = structuredClone(legacy);
      change(f);
      expect(toDocument(f).schema_version).toBe('1.1');
    }
  });

  it('round-trips form -> document -> form deterministically (also through YAML)', () => {
    for (const form of [fullForm(), blankForm(new Date('2026-01-01T00:00:00Z')), templateDraft().form]) {
      const doc = toDocument(form);
      const again = fromDocument(doc).form;
      expect(toDocument(again)).toEqual(doc);
      const viaYaml = fromDocument(parseYaml(dumpYaml(doc))).form;
      expect(documentKey(viaYaml)).toBe(documentKey(form));
      expect(dumpYaml(toDocument(viaYaml))).toBe(dumpYaml(doc));
    }
  });

  it('reads partial, odd and legacy documents without throwing', () => {
    expect(fromDocument(null).form).toMatchObject({ id: '', schemaVersion: '1.1', externalDelivery: 'deny', maxActions: '100', personaReplies: true });
    const { form, droppedAssertions } = fromDocument({
      schema_version: '1.0',
      scenario: {
        id: 7,
        version: 2,
        tags: 'solo',
        environment: { seed: true, clock: { start_at: '2026-01-01T00:00:00+05:30' } },
        personas: [{ id: 'a', behavior: { script: [{ once: false, on: { from: 'ghost' } }, 'junk'] } }, 'junk', { id: 'a' }],
        mailboxes: [{ owner: 'a', initial_messages: [{ fixture: 'f.eml' }, {}] }],
        events: [{ id: 'e', trigger: { type: 'at', at: 'x' }, event: { from: 'a', to: 'nobody' } }],
        agent: { mailbox: 'a', allowed_operations: ['read', { x: 1 }] },
        evaluation: { assertions: [{ id: 'x' }, { id: 'y' }], limits: {} },
      },
    });
    expect(droppedAssertions).toBe(2);
    expect(form).toMatchObject({ schemaVersion: '1.0', id: '7', version: '2', tags: ['solo'], seed: 'true', startAt: '2026-01-01T00:00:00+05:30' });
    expect(form.personas[0]?.script.map((r) => [r.once, r.from])).toEqual([[false, 'ghost'], [true, '']]);
    expect(form.personas[1]?.mode).toBe('silent');
    // duplicate ids: references go to the first persona with that id; unknown ids stay as text
    expect(form.mailboxes[0]?.owner).toBe(form.personas[0]?.uid);
    expect(form.mailboxes[0]?.messages).toEqual(['f.eml', '']);
    expect(form.events[0]?.to).toEqual(['nobody']);
    expect(form.allowed).toEqual(['read', '']);
    expect(refLabel(form, 'nobody')).toBe('nobody');
    expect(refLabel(form, form.personas[0]?.uid as string)).toBe('a');
    expect(fromDocument(TEMPLATE_DOCUMENT).droppedAssertions).toBe(0);
  });

  it('id edits never move references, even when ids collide or are prefixes of each other', () => {
    const f = fullForm();
    // type "bob" into alice's id one key at a time: "", "b", "bo", then it collides with bob
    for (const id of ['', 'b', 'bo', 'bob']) setPersonaId(f, 0, id, true);
    expect(f.personas[0]).toMatchObject({ id: 'bob', idTouched: true, email: 'alice@acme.test' });
    expect(f.mailboxes.map((m) => m.owner)).toEqual([f.personas[0]?.uid, f.personas[1]?.uid]);
    expect(f.agentMailbox).toBe(f.personas[0]?.uid);
    setPersonaId(f, 0, 'carol', true);
    const s = scenarioOf(f);
    expect(s.mailboxes.map((m) => m.owner)).toEqual(['carol', 'bob']);
    expect(s.agent.mailbox).toBe('carol');
    expect(s.events[1]?.event.to).toEqual(['carol', 'bob']);
    expect(s.personas[1]?.behavior.script?.[0]?.on.from).toBe('carol');
    expect(s.relationships[0]).toEqual({ source: 'carol', target: 'bob', type: 'vendor' });
    expect(s.projects[0]?.participants).toEqual(['carol', 'bob', 7]);
    // name-derived ids: a second persona deriving the same id takes none of the first one's references
    const g = blankForm();
    g.orgDomain = 'd.test';
    g.personas = [blankPersona([], 'd.test'), blankPersona(['persona'], 'd.test')];
    g.agentMailbox = g.personas[0]?.uid as string;
    setPersonaId(g, 0, 'dave', false);
    setPersonaId(g, 1, 'dave', false);
    expect(g.personas[0]).toMatchObject({ id: 'dave', idTouched: false, email: 'dave@d.test' });
    expect(g.agentMailbox).toBe(g.personas[0]?.uid);
  });

  it('removing a persona prunes every reference and says what will change', () => {
    const f = fullForm();
    expect(personaRemovalEffects(f, 0)).toEqual([
      'its mailbox and 1 initial message(s) will be removed',
      'the agent will have no mailbox',
      'event event-1 will no longer be sent to it',
      'event event-2 will no longer be sent to it',
      'reply rules of bob will match any sender',
    ]);
    expect(personaRemovalEffects(f, 1)).toEqual([
      'its mailbox will be removed',
      'event event-1 will have no sender',
      'event event-2 will have no sender',
      'event event-2 will no longer be sent to it',
      'event event-3 will have no sender',
    ]);
    f.events[0]!.id = '';
    expect(personaRemovalEffects(f, 1)[1]).toBe('event (unnamed) will have no sender');
    f.personas[1]!.id = '';
    expect(personaRemovalEffects(f, 0)).toContain('reply rules of (no id) will match any sender');
    removePersona(f, 0);
    expect(f.personas).toHaveLength(1);
    expect(f.mailboxes).toHaveLength(1);
    expect(f.agentMailbox).toBe('');
    expect(f.events.map((e) => e.to)).toEqual([[], [f.personas[0]?.uid], []]);
    expect(f.personas[0]?.script[0]?.from).toBe('');
    removePersona(f, 0);
    expect(f.events.map((e) => e.from)).toEqual(['', '', '']);
    expect(personaRemovalEffects(fromDocument({ scenario: { personas: [{ id: 'z' }] } }).form, 0)).toEqual([]);
  });

  it('lists referenced fixtures, personas that still need a mailbox and unknown keys', () => {
    const f = fullForm();
    expect(referencedFixtures(f)).toEqual(['fixtures/a.eml']);
    expect(personasNeedingMailbox(f)).toEqual([]);
    f.mailboxes = [];
    expect(personasNeedingMailbox(f).map((p) => p.id)).toEqual(['alice', 'bob']);
    f.agentMailbox = '';
    f.events = [];
    expect(personasNeedingMailbox(f)).toEqual([]);
    expect(unknownKeys(FULL)).toEqual([]);
    expect(
      unknownKeys({
        extra: 1,
        scenario: { personas: [{ id: 'a', behaviour: {}, behavior: { script: [{ on: { fromm: 'x' } }] } }, 'x'], agent: 5, relationships: [{ any: 1 }] },
      }),
    ).toEqual(['extra', 'scenario.personas.0.behaviour', 'scenario.personas.0.behavior.script.0.on.fromm']);
  });
});
