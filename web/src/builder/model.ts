// Form model of a scenario document (src/mailsim/scenario/schema.py) and the deterministic
// conversion form <-> document. Numbers are kept as strings in the form so that half-typed
// values reach the server validator (which reports them at the right path) instead of NaN.
//
// Canonical rules (toDocument): fixed key order; optional strings/numbers are omitted when
// empty; `behavior.mode` is omitted when silent; a single event recipient is written as a
// string; schema_version becomes "1.1" when 1.1 features are used; inline evaluation
// assertions are never written (answer keys are authored separately).
//
// Persona references (mailbox owner, agent mailbox, event from/to, reply-rule from,
// relationships and project participants) hold the persona's internal uid, resolved to its id
// in toDocument. Editing an id therefore never re-points another persona's references, and a
// reference to an unknown id keeps that id as text (the validator reports it). fromDocument(toDocument(f)) yields a
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
  /** Optional per-run resource caps; a missing or blank entry means the server default. */
  caps?: Partial<Record<CapKey, string>>;
  /** Parts of the document the form does not edit (kept verbatim; editable in YAML). */
  relationships: unknown[];
  projects: unknown[];
}

/** Optional per-run resource caps (scenario `evaluation.limits`); unset = server default. */
export const RUN_CAPS = [
  { key: 'max_agent_reads', label: 'Maximum agent reads', fallback: 10000, help: 'Reads and searches by the agent (each message fetched or listing page counts).' },
  { key: 'max_sent_copies', label: 'Maximum sent copies', fallback: 1000, help: 'Copies of sent mail an email client files into Sent over IMAP.' },
  { key: 'max_agent_events', label: 'Maximum agent events', fallback: 20000, help: 'Changes the agent causes in the run, counted since the last reset.' },
  { key: 'max_labels_per_message', label: 'Maximum labels per message', fallback: 50, help: 'Labels, keywords or categories the agent can leave on one message.' },
] as const;
export type CapKey = (typeof RUN_CAPS)[number]['key'];

let counter = 0;
// Per page load, so ids from a draft restored from sessionStorage never collide with new ones.
const LOAD = Math.floor(Math.random() * 36 ** 4).toString(36);
/** Local id for React keys (never written to the document). */
export function uid(): string {
  counter += 1;
  return `#u${LOAD}_${counter}`; // '#' never appears in scenario ids
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
    caps: {},
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

type Resolve = (ref: string) => string;

function personaDoc(p: PersonaForm, ref: Resolve): Json {
  const out: Json = { id: p.id };
  put(out, 'name', p.name);
  out.email = p.email;
  out.role = p.role;
  out.knowledge = [...p.knowledge];
  const behavior: Json = {};
  if (p.mode !== 'silent') behavior.mode = p.mode;
  put(behavior, 'response_style', p.response_style);
  putNum(behavior, 'response_delay_seconds', p.response_delay_seconds);
  if (p.script.length > 0) {
    behavior.script = p.script.map((r) => {
      const on: Json = {};
      put(on, 'from', ref(r.from));
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

function eventDoc(e: EventForm, ref: Resolve): Json {
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
      from: ref(e.from),
      to: e.to.length === 1 ? ref(e.to[0] as string) : e.to.map(ref),
      subject: e.subject,
      body: e.body,
    },
  };
}

/** Maps persona refs between uid (form) and id (document) inside relationships/projects. */
function mapRelations(items: unknown[], keys: string[], listKey: string | null, fn: Resolve): unknown[] {
  return items.map((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return item;
    const out: Json = { ...(item as Json) };
    for (const k of keys) if (typeof out[k] === 'string') out[k] = fn(out[k] as string);
    if (listKey && Array.isArray(out[listKey])) out[listKey] = (out[listKey] as unknown[]).map((v) => (typeof v === 'string' ? fn(v) : v));
    return out;
  });
}

/** Whether the form uses schema 1.1 features (persona names, reply modes, scripts). */
export function usesV11(f: ScenarioForm): boolean {
  return f.personas.some((p) => p.name.trim() !== '' || p.mode !== 'silent' || p.script.length > 0);
}

/** The scenario document for this form (canonical key order; never contains assertions). */
export function toDocument(f: ScenarioForm): Json {
  const ids = new Map(f.personas.map((p) => [p.uid, p.id]));
  const ref: Resolve = (v) => ids.get(v) ?? v;
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
    personas: f.personas.map((p) => personaDoc(p, ref)),
  };
  if (f.relationships.length > 0) scenario.relationships = mapRelations(f.relationships, ['source', 'target'], null, ref);
  if (f.projects.length > 0) scenario.projects = mapRelations(f.projects, [], 'participants', ref);
  scenario.mailboxes = f.mailboxes.map((m) => ({
    owner: ref(m.owner),
    initial_messages: m.messages.map((fixture) => ({ fixture })),
  }));
  scenario.events = f.events.map((e) => eventDoc(e, ref));
  scenario.agent = {
    mailbox: ref(f.agentMailbox),
    allowed_operations: [...f.allowed],
    prohibited_operations: [...f.prohibited],
  };
  const limits: Json = { max_agent_actions: num(f.maxActions), max_virtual_duration_seconds: num(f.maxDuration) };
  for (const cap of RUN_CAPS) {
    const value = f.caps?.[cap.key] ?? '';
    if (value.trim() !== '') limits[cap.key] = num(value);
  }
  scenario.evaluation = { limits };
  return { schema_version: usesV11(f) ? SCHEMA_VERSION : f.schemaVersion, scenario };
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
  const personas: PersonaForm[] = arr(s.personas).map((raw) => {
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
  });
  const uids = new Map<string, string>();
  for (const p of [...personas].reverse()) uids.set(p.id, p.uid); // first persona wins on duplicates
  const ref: Resolve = (id) => (id ? (uids.get(id) ?? id) : id); // a missing ref never binds to an id-less persona
  for (const p of personas) for (const r of p.script) r.from = r.from && ref(r.from);
  const form: ScenarioForm = {
    schemaVersion: str(root.schema_version) || SCHEMA_VERSION,
    id: str(s.id),
    version: str(s.version),
    title: str(s.title),
    tags: strList(s.tags),
    orgDomain: str(org.domain),
    orgName: str(org.name),
    policies: arr(org.policies).map((p) => ({ uid: uid(), id: str(obj(p).id), rule: str(obj(p).rule) })),
    personas,
    mailboxes: arr(s.mailboxes).map((raw) => {
      const m = obj(raw);
      return { uid: uid(), owner: ref(str(m.owner)), messages: arr(m.initial_messages).map((im) => str(obj(im).fixture)) };
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
        from: ref(str(ev.from)),
        to: strList(ev.to).map(ref),
        subject: str(ev.subject),
        body: str(ev.body),
      };
    }),
    agentMailbox: ref(str(agent.mailbox)),
    allowed: strList(agent.allowed_operations),
    prohibited: strList(agent.prohibited_operations),
    seed: str(env.seed),
    startAt: str(obj(env.clock).start_at),
    externalDelivery: str(delivery.external_delivery) || 'deny',
    personaReplies: delivery.persona_replies !== 'disabled',
    maxActions: str(limits.max_agent_actions) || '100',
    maxDuration: str(limits.max_virtual_duration_seconds) || '86400',
    caps: Object.fromEntries(RUN_CAPS.filter((cap) => str(limits[cap.key]) !== '').map((cap) => [cap.key, str(limits[cap.key])])),
    relationships: mapRelations(arr(s.relationships), ['source', 'target'], null, ref),
    projects: mapRelations(arr(s.projects), [], 'participants', ref),
  };
  return { form, droppedAssertions: arr(evaluation.assertions).length };
}

/** Stable text of the document, used to compare drafts and key validation requests. */
export function documentKey(f: ScenarioForm): string {
  return JSON.stringify(toDocument(f));
}

// ---- edits ------------------------------------------------------------------------------------
/** Sets a persona's id (references follow automatically: they hold the uid) and the derived email. */
export function setPersonaId(f: ScenarioForm, index: number, id: string, touched: boolean): void {
  const p = f.personas[index] as PersonaForm;
  p.id = id;
  p.idTouched = p.idTouched || touched;
  if (!p.emailTouched) p.email = defaultEmail(id, f.orgDomain);
}

/** What removing persona `index` changes elsewhere (for a confirmation). */
export function personaRemovalEffects(f: ScenarioForm, index: number): string[] {
  const p = f.personas[index] as PersonaForm;
  const out: string[] = [];
  const box = f.mailboxes.find((m) => m.owner === p.uid);
  if (box) out.push(`its mailbox${box.messages.length > 0 ? ` and ${box.messages.length} initial message(s)` : ''} will be removed`);
  if (f.agentMailbox === p.uid) out.push('the agent will have no mailbox');
  for (const e of f.events) {
    if (e.from === p.uid) out.push(`event ${e.id || '(unnamed)'} will have no sender`);
    if (e.to.includes(p.uid)) out.push(`event ${e.id || '(unnamed)'} will no longer be sent to it`);
  }
  for (const other of f.personas) {
    if (other.script.some((r) => r.from === p.uid)) out.push(`reply rules of ${other.id || '(no id)'} will match any sender`);
  }
  return out;
}

/** Removes persona `index` and every reference to it. */
export function removePersona(f: ScenarioForm, index: number): void {
  const [p] = f.personas.splice(index, 1) as [PersonaForm];
  f.mailboxes = f.mailboxes.filter((m) => m.owner !== p.uid);
  if (f.agentMailbox === p.uid) f.agentMailbox = '';
  for (const e of f.events) {
    if (e.from === p.uid) e.from = '';
    e.to = e.to.filter((t) => t !== p.uid);
  }
  for (const other of f.personas) for (const r of other.script) if (r.from === p.uid) r.from = '';
}

/** Every fixture path referenced by a mailbox. */
export function referencedFixtures(f: ScenarioForm): string[] {
  return f.mailboxes.flatMap((m) => m.messages);
}

/** Personas that receive events (or are the agent's) but have no mailbox. */
export function personasNeedingMailbox(f: ScenarioForm): PersonaForm[] {
  const owners = new Set(f.mailboxes.map((m) => m.owner));
  const needed = new Set<string>();
  if (f.agentMailbox) needed.add(f.agentMailbox);
  for (const e of f.events) for (const t of e.to) needed.add(t);
  return f.personas.filter((p) => needed.has(p.uid) && !owners.has(p.uid));
}

/** Display text for a persona reference (uid or raw id). */
export function refLabel(f: ScenarioForm, ref: string): string {
  const p = f.personas.find((x) => x.uid === ref);
  return p ? personaLabel(p) : ref;
}

/** Label for a persona in pickers: `Name <email>` (or the id when unnamed). */
export function personaLabel(p: { id: string; name: string; email: string }): string {
  const who = p.name.trim() || p.id || '(no id)';
  return p.email ? `${who} <${p.email}>` : who;
}

// ---- keys the form understands (to report what a pasted YAML document would lose) -----------
type Shape = true | { [key: string]: Shape } | [Shape];
const RULE: Shape = { on: { from: true, subject_contains: true, body_contains: true }, reply: { subject: true, body: true }, delay_seconds: true, once: true };
const KNOWN: Shape = {
  schema_version: true,
  scenario: {
    id: true,
    version: true,
    title: true,
    tags: true,
    environment: { seed: true, clock: { start_at: true, mode: true }, delivery: { external_delivery: true, persona_replies: true } },
    organization: { domain: true, name: true, policies: [{ id: true, rule: true }] },
    personas: [
      {
        id: true,
        name: true,
        email: true,
        role: true,
        knowledge: true,
        behavior: { mode: true, response_style: true, response_delay_seconds: true, script: [RULE] },
      },
    ],
    relationships: true,
    projects: true,
    mailboxes: [{ owner: true, initial_messages: [{ fixture: true }] }],
    events: [
      {
        id: true,
        trigger: { type: true, at: true, seconds: true, action: true, delay_seconds: true },
        event: { type: true, from: true, to: true, subject: true, body: true },
      },
    ],
    agent: { mailbox: true, allowed_operations: true, prohibited_operations: true },
    evaluation: { assertions: true, limits: { max_agent_actions: true, max_virtual_duration_seconds: true, ...Object.fromEntries(RUN_CAPS.map((cap) => [cap.key, true])) } },
  },
};

/** Dotted paths of keys in `doc` that the scenario format does not have (the form drops them). */
export function unknownKeys(doc: unknown, shape: Shape = KNOWN, path = ''): string[] {
  if (shape === true) return [];
  if (Array.isArray(shape)) {
    return arr(doc).flatMap((item, i) => unknownKeys(item, shape[0], `${path}.${i}`));
  }
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) return [];
  return Object.entries(doc as Json).flatMap(([k, v]) => {
    const p = path ? `${path}.${k}` : k;
    const sub = shape[k];
    return sub === undefined ? [p] : unknownKeys(v, sub, p);
  });
}
