// Form model of a scenario document (src/mailsim/scenario/schema.py) and the deterministic
// conversion form <-> document. Numbers are kept as strings in the form so that half-typed
// values reach the server validator (which reports them at the right path) instead of NaN.
//
// Canonical rules (toDocument): fixed key order; optional strings/numbers are omitted when
// empty; a single event recipient is written as a string; inline evaluation assertions are
// never written (answer keys are authored separately). fromDocument(toDocument(f)) yields a
// form whose toDocument is identical, so the YAML round trip is stable.

export type Json = Record<string, unknown>;

export interface PolicyForm {
  uid: string;
  id: string;
  rule: string;
}

export interface ScriptRuleForm {
  uid: string;
  from: string;
  subject_contains: string;
  body_contains: string;
  reply_subject: string;
  reply_body: string;
  delay_seconds: string;
  once: boolean;
}

export interface PersonaForm {
  uid: string;
  id: string;
  /** The id was typed by hand (stop deriving it from the name). */
  idTouched: boolean;
  name: string;
  email: string;
  /** The email was typed by hand (stop deriving it from id + domain). */
  emailTouched: boolean;
  role: string;
  knowledge: string[];
  mode: string;
  response_style: string;
  response_delay_seconds: string;
  script: ScriptRuleForm[];
}

export interface MailboxForm {
  uid: string;
  owner: string;
  /** Fixture paths of the initial messages, in delivery order. */
  messages: string[];
}

export interface EventForm {
  uid: string;
  id: string;
  triggerType: string;
  at: string;
  seconds: string;
  action: string;
  delay_seconds: string;
  from: string;
  to: string[];
  subject: string;
  body: string;
}

export interface ScenarioForm {
  schemaVersion: string;
  id: string;
  version: string;
  title: string;
  tags: string[];
  orgDomain: string;
  orgName: string;
  policies: PolicyForm[];
  personas: PersonaForm[];
  mailboxes: MailboxForm[];
  events: EventForm[];
  agentMailbox: string;
  allowed: string[];
  prohibited: string[];
  seed: string;
  startAt: string;
  externalDelivery: string;
  personaReplies: boolean;
  maxActions: string;
  maxDuration: string;
  /** Parts of the document the form does not edit (kept verbatim; editable in YAML). */
  relationships: unknown[];
  projects: unknown[];
}

let counter = 0;
// Per page load, so ids from a draft restored from sessionStorage never collide with new ones.
const LOAD = Math.floor(Math.random() * 36 ** 4).toString(36);
/** Local id for React keys (never written to the document). */
export function uid(): string {
  counter += 1;
  return `u${LOAD}_${counter}`;
}

export const SCHEMA_VERSION = '1.1';

/** "Finance Manager" -> "finance_manager". */
export function slugId(text: string): string {
  return text
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}

export function defaultEmail(id: string, domain: string): string {
  return id && domain ? `${id}@${domain}` : '';
}

/** Current time rounded down to the hour, as `YYYY-MM-DDTHH:00:00Z`. */
export function defaultStartAt(now: Date = new Date()): string {
  return `${now.toISOString().slice(0, 13)}:00:00Z`;
}

/** Returns `base`, or `base_2`, `base_3`... so that it is not in `taken`. */
export function uniqueId(base: string, taken: readonly string[], sep = '_'): string {
  if (!taken.includes(base)) return base;
  let n = 2;
  while (taken.includes(`${base}${sep}${n}`)) n += 1;
  return `${base}${sep}${n}`;
}

export function blankPersona(taken: readonly string[] = [], domain = ''): PersonaForm {
  const id = uniqueId('persona', taken);
  return {
    uid: uid(),
    id,
    idTouched: false,
    name: '',
    email: defaultEmail(id, domain),
    emailTouched: false,
    role: '',
    knowledge: [],
    mode: 'silent',
    response_style: '',
    response_delay_seconds: '',
    script: [],
  };
}

export function blankRule(): ScriptRuleForm {
  return {
    uid: uid(),
    from: '',
    subject_contains: '',
    body_contains: '',
    reply_subject: '',
    reply_body: '',
    delay_seconds: '',
    once: true,
  };
}

export function blankEvent(taken: readonly string[], from = '', to: string[] = []): EventForm {
  let n = 1;
  while (taken.includes(`event-${n}`)) n += 1;
  return {
    uid: uid(),
    id: `event-${n}`,
    triggerType: 'after_seconds',
    at: '',
    seconds: '600',
    action: '',
    delay_seconds: '',
    from,
    to,
    subject: '',
    body: '',
  };
}

export function blankForm(now: Date = new Date()): ScenarioForm {
  return {
    schemaVersion: SCHEMA_VERSION,
    id: '',
    version: '1',
    title: '',
    tags: [],
    orgDomain: '',
    orgName: '',
    policies: [],
    personas: [],
    mailboxes: [],
    events: [],
    agentMailbox: '',
    allowed: ['read', 'search', 'reply'],
    prohibited: ['external_delivery'],
    seed: '1',
    startAt: defaultStartAt(now),
    externalDelivery: 'deny',
    personaReplies: true,
    maxActions: '100',
    maxDuration: '86400',
    relationships: [],
    projects: [],
  };
}

// ---- form -> document -----------------------------------------------------------------
/** Integer-looking text becomes a number; anything else stays text for the validator. */
export function num(text: string): number | string {
  const t = text.trim();
  return /^-?\d+$/.test(t) ? Number(t) : t;
}

function put(target: Json, key: string, value: string): void {
  if (value.trim() !== '') target[key] = value;
}

function putNum(target: Json, key: string, value: string): void {
  if (value.trim() !== '') target[key] = num(value);
}

function personaDoc(p: PersonaForm): Json {
  const out: Json = { id: p.id };
  put(out, 'name', p.name);
  out.email = p.email;
  out.role = p.role;
  out.knowledge = [...p.knowledge];
  const behavior: Json = { mode: p.mode };
  put(behavior, 'response_style', p.response_style);
  putNum(behavior, 'response_delay_seconds', p.response_delay_seconds);
  if (p.script.length > 0) {
    behavior.script = p.script.map((r) => {
      const on: Json = {};
      put(on, 'from', r.from);
      put(on, 'subject_contains', r.subject_contains);
      put(on, 'body_contains', r.body_contains);
      const reply: Json = {};
      put(reply, 'subject', r.reply_subject);
      reply.body = r.reply_body;
      const rule: Json = { on, reply };
      putNum(rule, 'delay_seconds', r.delay_seconds);
      rule.once = r.once;
      return rule;
    });
  }
  out.behavior = behavior;
  return out;
}

function eventDoc(e: EventForm): Json {
  const trigger: Json = { type: e.triggerType };
  if (e.triggerType === 'at') put(trigger, 'at', e.at);
  if (e.triggerType === 'after_seconds') putNum(trigger, 'seconds', e.seconds);
  if (e.triggerType === 'after_agent_action') put(trigger, 'action', e.action);
  putNum(trigger, 'delay_seconds', e.delay_seconds);
  return {
    id: e.id,
    trigger,
    event: {
      type: 'email.receive',
      from: e.from,
      to: e.to.length === 1 ? e.to[0] : [...e.to],
      subject: e.subject,
      body: e.body,
    },
  };
}

/** The scenario document for this form (canonical key order; never contains assertions). */
export function toDocument(f: ScenarioForm): Json {
  const organization: Json = { domain: f.orgDomain };
  put(organization, 'name', f.orgName);
  organization.policies = f.policies.map((p) => ({ id: p.id, rule: p.rule }));
  const scenario: Json = {
    id: f.id,
    version: num(f.version),
    title: f.title,
    tags: [...f.tags],
    environment: {
      seed: num(f.seed),
      clock: { start_at: f.startAt },
      delivery: {
        external_delivery: f.externalDelivery,
        persona_replies: f.personaReplies ? 'enabled' : 'disabled',
      },
    },
    organization,
    personas: f.personas.map(personaDoc),
  };
  if (f.relationships.length > 0) scenario.relationships = f.relationships;
  if (f.projects.length > 0) scenario.projects = f.projects;
  scenario.mailboxes = f.mailboxes.map((m) => ({
    owner: m.owner,
    initial_messages: m.messages.map((fixture) => ({ fixture })),
  }));
  scenario.events = f.events.map(eventDoc);
  scenario.agent = {
    mailbox: f.agentMailbox,
    allowed_operations: [...f.allowed],
    prohibited_operations: [...f.prohibited],
  };
  scenario.evaluation = { limits: { max_agent_actions: num(f.maxActions), max_virtual_duration_seconds: num(f.maxDuration) } };
  return { schema_version: f.schemaVersion, scenario };
}

// ---- document -> form -----------------------------------------------------------------
function obj(v: unknown): Json {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Json) : {};
}

function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

/** Text for a form field: strings as-is, numbers/booleans stringified, anything else empty. */
function str(v: unknown): string {
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  return '';
}

function strList(v: unknown): string[] {
  if (typeof v === 'string') return [v];
  return arr(v).map(str);
}

export interface Imported {
  form: ScenarioForm;
  /** Inline evaluation assertions found (and dropped: they belong in an answer key). */
  droppedAssertions: number;
}

/** Builds a form from any (possibly partial or invalid) scenario document. */
export function fromDocument(doc: unknown): Imported {
  const root = obj(doc);
  const s = obj(root.scenario);
  const env = obj(s.environment);
  const delivery = obj(env.delivery);
  const org = obj(s.organization);
  const agent = obj(s.agent);
  const evaluation = obj(s.evaluation);
  const limits = obj(evaluation.limits);
  const form: ScenarioForm = {
    schemaVersion: str(root.schema_version) || SCHEMA_VERSION,
    id: str(s.id),
    version: str(s.version),
    title: str(s.title),
    tags: strList(s.tags),
    orgDomain: str(org.domain),
    orgName: str(org.name),
    policies: arr(org.policies).map((p) => ({ uid: uid(), id: str(obj(p).id), rule: str(obj(p).rule) })),
    personas: arr(s.personas).map((raw) => {
      const p = obj(raw);
      const b = obj(p.behavior);
      return {
        uid: uid(),
        id: str(p.id),
        idTouched: true,
        name: str(p.name),
        email: str(p.email),
        emailTouched: true,
        role: str(p.role),
        knowledge: strList(p.knowledge),
        mode: str(b.mode) || 'silent',
        response_style: str(b.response_style),
        response_delay_seconds: str(b.response_delay_seconds),
        script: arr(b.script).map((rr) => {
          const r = obj(rr);
          const on = obj(r.on);
          const reply = obj(r.reply);
          return {
            uid: uid(),
            from: str(on.from),
            subject_contains: str(on.subject_contains),
            body_contains: str(on.body_contains),
            reply_subject: str(reply.subject),
            reply_body: str(reply.body),
            delay_seconds: str(r.delay_seconds),
            once: r.once !== false,
          };
        }),
      };
    }),
    mailboxes: arr(s.mailboxes).map((raw) => {
      const m = obj(raw);
      return { uid: uid(), owner: str(m.owner), messages: arr(m.initial_messages).map((im) => str(obj(im).fixture)) };
    }),
    events: arr(s.events).map((raw) => {
      const e = obj(raw);
      const t = obj(e.trigger);
      const ev = obj(e.event);
      return {
        uid: uid(),
        id: str(e.id),
        triggerType: str(t.type),
        at: str(t.at),
        seconds: str(t.seconds),
        action: str(t.action),
        delay_seconds: str(t.delay_seconds),
        from: str(ev.from),
        to: strList(ev.to),
        subject: str(ev.subject),
        body: str(ev.body),
      };
    }),
    agentMailbox: str(agent.mailbox),
    allowed: strList(agent.allowed_operations),
    prohibited: strList(agent.prohibited_operations),
    seed: str(env.seed),
    startAt: str(obj(env.clock).start_at),
    externalDelivery: str(delivery.external_delivery) || 'deny',
    personaReplies: delivery.persona_replies !== 'disabled',
    maxActions: str(limits.max_agent_actions) || '100',
    maxDuration: str(limits.max_virtual_duration_seconds) || '86400',
    relationships: arr(s.relationships),
    projects: arr(s.projects),
  };
  return { form, droppedAssertions: arr(evaluation.assertions).length };
}

/** Stable text of the document, used to compare drafts and key validation requests. */
export function documentKey(f: ScenarioForm): string {
  return JSON.stringify(toDocument(f));
}

// ---- edits that keep references consistent -------------------------------------------------
/** Renames a persona id everywhere it is referenced (mailboxes, agent, events, scripts). */
export function renamePersonaRefs(f: ScenarioForm, from: string, to: string): void {
  if (!from || from === to) return;
  const swap = (v: string) => (v === from ? to : v);
  for (const m of f.mailboxes) m.owner = swap(m.owner);
  f.agentMailbox = swap(f.agentMailbox);
  for (const e of f.events) {
    e.from = swap(e.from);
    e.to = e.to.map(swap);
  }
  for (const p of f.personas) for (const r of p.script) r.from = swap(r.from);
}

/** Sets a persona's id, keeping references and the derived email in sync. */
export function setPersonaId(f: ScenarioForm, index: number, id: string, touched: boolean): void {
  const p = f.personas[index] as PersonaForm;
  renamePersonaRefs(f, p.id, id);
  p.id = id;
  p.idTouched = p.idTouched || touched;
  if (!p.emailTouched) p.email = defaultEmail(id, f.orgDomain);
}

/** Every fixture path referenced by a mailbox. */
export function referencedFixtures(f: ScenarioForm): string[] {
  return f.mailboxes.flatMap((m) => m.messages);
}

/** Persona ids that send or receive scheduled events but have no mailbox. */
export function personasNeedingMailbox(f: ScenarioForm): string[] {
  const owners = new Set(f.mailboxes.map((m) => m.owner));
  const needed = new Set<string>();
  if (f.agentMailbox) needed.add(f.agentMailbox);
  for (const e of f.events) for (const t of e.to) needed.add(t);
  return f.personas.map((p) => p.id).filter((id) => needed.has(id) && !owners.has(id));
}

/** Label for a persona in pickers: `Name <email>` (or the id when unnamed). */
export function personaLabel(p: { id: string; name: string; email: string }): string {
  const who = p.name.trim() || p.id || '(no id)';
  return p.email ? `${who} <${p.email}>` : who;
}
