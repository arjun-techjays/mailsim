import { dumpYaml, parseYaml } from '../mocks/builder';
import {
  blankEvent,
  blankForm,
  blankPersona,
  blankRule,
  defaultEmail,
  defaultStartAt,
  documentKey,
  fromDocument,
  num,
  personaLabel,
  personasNeedingMailbox,
  referencedFixtures,
  renamePersonaRefs,
  setPersonaId,
  slugId,
  toDocument,
  uid,
  uniqueId,
  type ScenarioForm,
} from './model';
import { TEMPLATE_DOCUMENT, templateDraft } from './template';

function fullForm(): ScenarioForm {
  const f = blankForm(new Date('2026-10-15T09:41:12Z'));
  f.id = 'x.full';
  f.title = 'Full';
  f.tags = ['a'];
  f.orgDomain = 'acme.test';
  f.orgName = 'Acme';
  f.policies = [{ uid: uid(), id: 'p1', rule: 'Be careful' }];
  const a = blankPersona([], 'acme.test');
  setPersonaFields(a, { id: 'alice', name: 'Alice', email: 'alice@acme.test', emailTouched: true, role: 'Finance', knowledge: ['k1'] });
  const b = blankPersona(['persona'], 'acme.test');
  setPersonaFields(b, { id: 'bob', name: '', email: 'bob@acme.test', role: 'Vendor', mode: 'scripted' });
  b.response_delay_seconds = '30';
  b.response_style = 'terse';
  const rule = blankRule();
  rule.from = 'alice';
  rule.subject_contains = 'INV';
  rule.body_contains = 'pay';
  rule.reply_subject = 'Re: INV';
  rule.reply_body = 'ok';
  rule.delay_seconds = '5';
  rule.once = false;
  b.script = [rule, blankRule()];
  f.personas = [a, b];
  f.mailboxes = [
    { uid: uid(), owner: 'alice', messages: ['fixtures/a.eml'] },
    { uid: uid(), owner: 'bob', messages: [] },
  ];
  const e1 = blankEvent([], 'bob', ['alice']);
  const e2 = blankEvent(['event-1'], 'bob', ['alice', 'bob']);
  e2.triggerType = 'at';
  e2.at = '2026-10-15T10:00:00Z';
  e2.delay_seconds = '7';
  const e3 = blankEvent(['event-1', 'event-2'], 'bob', []);
  e3.triggerType = 'after_agent_action';
  e3.action = 'send_reply';
  f.events = [e1, e2, e3];
  f.agentMailbox = 'alice';
  f.relationships = [{ source: 'alice', target: 'bob', type: 'vendor' }];
  f.projects = [{ id: 'p', name: 'P' }];
  f.personaReplies = false;
  return f;
}

function setPersonaFields(p: ReturnType<typeof blankPersona>, v: Partial<ReturnType<typeof blankPersona>>) {
  Object.assign(p, v);
}

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

  it('writes a canonical document: optional parts omitted, single recipient as a string, no assertions', () => {
    const doc = toDocument(fullForm()) as { scenario: Record<string, unknown> };
    const s = doc.scenario;
    expect(Object.keys(s)).toEqual([
      'id', 'version', 'title', 'tags', 'environment', 'organization', 'personas', 'relationships', 'projects',
      'mailboxes', 'events', 'agent', 'evaluation',
    ]);
    expect(s.evaluation).toEqual({ limits: { max_agent_actions: 100, max_virtual_duration_seconds: 86400 } });
    expect(s.environment).toEqual({
      seed: 1,
      clock: { start_at: '2026-10-15T09:00:00Z' },
      delivery: { external_delivery: 'deny', persona_replies: 'disabled' },
    });
    expect(s.organization).toEqual({ domain: 'acme.test', name: 'Acme', policies: [{ id: 'p1', rule: 'Be careful' }] });
    const [alice, bob] = s.personas as Record<string, unknown>[];
    expect(alice).toEqual({ id: 'alice', name: 'Alice', email: 'alice@acme.test', role: 'Finance', knowledge: ['k1'], behavior: { mode: 'silent' } });
    expect(bob).toEqual({
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
    });
    const events = s.events as { trigger: unknown; event: { to: unknown } }[];
    expect(events.map((e) => e.trigger)).toEqual([
      { type: 'after_seconds', seconds: 600 },
      { type: 'at', at: '2026-10-15T10:00:00Z', delay_seconds: 7 },
      { type: 'after_agent_action', action: 'send_reply' },
    ]);
    expect(events.map((e) => e.event.to)).toEqual(['alice', ['alice', 'bob'], []]);

    const plain = blankForm();
    expect(toDocument(plain).scenario).not.toHaveProperty('relationships');
    expect((toDocument(plain).scenario as { organization: unknown }).organization).toEqual({ domain: '', policies: [] });
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
        personas: [{ id: 'a', behavior: { script: [{ once: false }, 'junk'] } }, 'junk'],
        mailboxes: [{ owner: 'a', initial_messages: [{ fixture: 'f.eml' }, {}] }],
        events: [{ id: 'e', trigger: { type: 'at', at: 'x' }, event: { from: 'a', to: 'a' } }],
        agent: { mailbox: 'a', allowed_operations: ['read', { x: 1 }] },
        evaluation: { assertions: [{ id: 'x' }, { id: 'y' }], limits: {} },
      },
    });
    expect(droppedAssertions).toBe(2);
    expect(form).toMatchObject({ schemaVersion: '1.0', id: '7', version: '2', tags: ['solo'], seed: 'true', startAt: '2026-01-01T00:00:00+05:30' });
    expect(form.personas[0]?.script.map((r) => r.once)).toEqual([false, true]);
    expect(form.personas[1]?.mode).toBe('silent');
    expect(form.mailboxes[0]?.messages).toEqual(['f.eml', '']);
    expect(form.events[0]?.to).toEqual(['a']);
    expect(form.allowed).toEqual(['read', '']);
    expect(fromDocument(TEMPLATE_DOCUMENT).droppedAssertions).toBe(0);
  });

  it('keeps references in sync when a persona id changes', () => {
    const f = fullForm();
    renamePersonaRefs(f, 'alice', 'alice'); // no-op
    renamePersonaRefs(f, '', 'x'); // no-op
    setPersonaId(f, 0, 'carol', true);
    expect(f.personas[0]).toMatchObject({ id: 'carol', idTouched: true, email: 'alice@acme.test' });
    expect(f.mailboxes[0]?.owner).toBe('carol');
    expect(f.agentMailbox).toBe('carol');
    expect(f.events[1]?.to).toEqual(['carol', 'bob']);
    expect(f.personas[1]?.script[0]?.from).toBe('carol');
    setPersonaId(f, 0, '', true);
    expect(f.agentMailbox).toBe('carol');
    setPersonaId(f, 0, '', true);
    setPersonaId(f, 0, 'dan', true);
    expect(f.agentMailbox).toBe('dan');
    expect(f.personas[0]?.lastId).toBeUndefined();
    const h = fromDocument({ scenario: { personas: [{ email: 'x@y' }] } }).form;
    setPersonaId(h, 0, 'x', true);
    expect(h.personas[0]?.id).toBe('x');
    const g = blankForm();
    g.orgDomain = 'd.test';
    g.personas = [blankPersona([], 'd.test')];
    setPersonaId(g, 0, 'dave', false);
    expect(g.personas[0]).toMatchObject({ id: 'dave', idTouched: false, email: 'dave@d.test' });
  });

  it('lists referenced fixtures and personas that still need a mailbox', () => {
    const f = fullForm();
    expect(referencedFixtures(f)).toEqual(['fixtures/a.eml']);
    expect(personasNeedingMailbox(f)).toEqual([]);
    f.mailboxes = [];
    expect(personasNeedingMailbox(f)).toEqual(['alice', 'bob']);
    f.agentMailbox = '';
    f.events = [];
    expect(personasNeedingMailbox(f)).toEqual([]);
  });
});
