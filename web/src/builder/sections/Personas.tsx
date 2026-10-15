import { useBuilder } from '../context';
import { Group, RadioGroup, SelectField, StringListField, TextArea, TextField } from '../fields';
import { MODE_HELP } from '../help';
import {
  blankPersona,
  blankRule,
  personaLabel,
  personaRemovalEffects,
  removePersona,
  setPersonaId,
  slugId,
  type PersonaForm,
  type ScenarioForm,
} from '../model';

function move<T>(list: T[], from: number, to: number): void {
  const [item] = list.splice(from, 1);
  list.splice(to, 0, item as T);
}

function ScriptRules({ index }: { index: number }) {
  const { draft, updateForm } = useBuilder();
  const persona = draft.form.personas[index] as PersonaForm;
  const base = `scenario.personas.${index}.behavior.script`;
  const edit = (j: number, fn: (r: PersonaForm['script'][number]) => void) =>
    updateForm((d) => fn((d.personas[index] as PersonaForm).script[j] as PersonaForm['script'][number]));
  const others = draft.form.personas.filter((_, i) => i !== index);
  return (
    <Group
      path={base}
      legend="Reply rules"
      help="When a message arrives that matches a rule, this persona replies with the given text. Empty conditions match everything; each rule fires at most once unless you untick “Only once”."
    >
      {persona.script.length === 0 && <p className="muted">No rules yet: this persona will not reply. Add a rule.</p>}
      {persona.script.map((r, j) => (
        <div className="b-item" key={r.uid}>
          <h5>Rule {j + 1}</h5>
          <SelectField
            path={`${base}.${j}.on.from`}
            label="When the message is from"
            value={r.from}
            placeholder="Anyone"
            options={others.map((p) => ({ value: p.uid, label: personaLabel(p) }))}
            onChange={(v) => edit(j, (x) => void (x.from = v))}
          />
          <TextField path={`${base}.${j}.on.subject_contains`} label="and the subject contains" value={r.subject_contains} onChange={(v) => edit(j, (x) => void (x.subject_contains = v))} />
          <TextField path={`${base}.${j}.on.body_contains`} label="and the body contains" value={r.body_contains} onChange={(v) => edit(j, (x) => void (x.body_contains = v))} />
          <TextField
            path={`${base}.${j}.reply.subject`}
            label="Reply subject (optional)"
            value={r.reply_subject}
            placeholder="Re: <original subject>"
            onChange={(v) => edit(j, (x) => void (x.reply_subject = v))}
          />
          <TextArea path={`${base}.${j}.reply.body`} label="Reply text" value={r.reply_body} onChange={(v) => edit(j, (x) => void (x.reply_body = v))} />
          <TextField
            path={`${base}.${j}.delay_seconds`}
            label="Reply after (seconds, optional)"
            type="number"
            min={0}
            value={r.delay_seconds}
            help="Defaults to the persona's response delay."
            onChange={(v) => edit(j, (x) => void (x.delay_seconds = v))}
          />
          <div className="checkbox">
            <input id={`${base}-${r.uid}-once`} type="checkbox" checked={r.once} onChange={(e) => edit(j, (x) => void (x.once = e.target.checked))} />
            <label htmlFor={`${base}-${r.uid}-once`}>Only once</label>
          </div>
          <button type="button" onClick={() => updateForm((d) => void (d.personas[index] as PersonaForm).script.splice(j, 1))}>
            Remove rule {j + 1}
          </button>
        </div>
      ))}
      <button type="button" onClick={() => updateForm((d) => void (d.personas[index] as PersonaForm).script.push(blankRule()))}>
        Add reply rule
      </button>
    </Group>
  );
}

function PersonaCard({ index }: { index: number }) {
  const { draft, updateForm, meta } = useBuilder();
  const f = draft.form;
  const p = f.personas[index] as PersonaForm;
  const base = `scenario.personas.${index}`;
  const edit = (fn: (x: PersonaForm, d: ScenarioForm) => void) => updateForm((d) => fn(d.personas[index] as PersonaForm, d));
  const n = index + 1;
  return (
    <Group path={base} legend={`Persona ${n}: ${p.name || p.id || 'unnamed'}`} className="b-card">
      <TextField
        path={`${base}.name`}
        label="Display name"
        value={p.name}
        placeholder="Finance Manager"
        help="Shown in From/To headers. The id below is derived from it until you edit the id."
        onChange={(v) =>
          edit((x, d) => {
            x.name = v;
            if (!x.idTouched && slugId(v)) setPersonaId(d, index, slugId(v), false);
          })
        }
      />
      <TextField
        path={`${base}.id`}
        label="Persona id"
        required
        value={p.id}
        help="Used to refer to this persona elsewhere (mailboxes, events). Letters, digits and underscores."
        onChange={(v) => edit((_x, d) => setPersonaId(d, index, v, true))}
      />
      <TextField
        path={`${base}.email`}
        label="Email address"
        type="email"
        required
        value={p.email}
        help={f.orgDomain ? `Defaults to ${p.id || '<id>'}@${f.orgDomain}.` : 'Set the organisation’s mail domain to get a default address.'}
        onChange={(v) =>
          edit((x) => {
            x.email = v;
            x.emailTouched = true;
          })
        }
      />
      <TextField path={`${base}.role`} label="Role" required value={p.role} placeholder="Finance Manager" help="Their job, in a few words." onChange={(v) => edit((x) => void (x.role = v))} />
      <StringListField
        path={`${base}.knowledge`}
        label="Knowledge"
        items={p.knowledge}
        placeholder="invoice approval policy"
        help="Facts this persona knows (used by LLM personas and graders)."
        onChange={(v) => edit((x) => void (x.knowledge = v))}
      />
      <RadioGroup
        path={`${base}.behavior`}
        legend="How this persona replies"
        value={p.mode}
        options={meta.behavior_modes.map((m) => ({ value: m, label: m, help: MODE_HELP[m] }))}
        onChange={(v) => edit((x) => void (x.mode = v))}
      />
      {p.mode !== 'silent' && (
        <TextField
          path={`${base}.behavior.response_delay_seconds`}
          label="Response delay (seconds)"
          type="number"
          min={0}
          value={p.response_delay_seconds}
          placeholder="0"
          help="Virtual seconds before a reply is delivered."
          onChange={(v) => edit((x) => void (x.response_delay_seconds = v))}
        />
      )}
      {p.mode === 'llm' && (
        <TextField
          path={`${base}.behavior.response_style`}
          label="Response style"
          value={p.response_style}
          placeholder="concise, professional"
          onChange={(v) => edit((x) => void (x.response_style = v))}
        />
      )}
      {(p.mode === 'scripted' || p.script.length > 0) && <ScriptRules index={index} />}
      <div className="row">
        <button type="button" disabled={index === 0} onClick={() => updateForm((d) => move(d.personas, index, index - 1))}>
          Move persona {n} up
        </button>
        <button type="button" disabled={index === f.personas.length - 1} onClick={() => updateForm((d) => move(d.personas, index, index + 1))}>
          Move persona {n} down
        </button>
        <button
          type="button"
          onClick={() => {
            const effects = personaRemovalEffects(f, index);
            const ask = `Remove ${personaLabel(p)}?\n\nAlso:\n- ${effects.join('\n- ')}`;
            if (effects.length > 0 && !window.confirm(ask)) return;
            updateForm((d) => removePersona(d, index));
          }}
        >
          Remove persona {n}
        </button>
      </div>
    </Group>
  );
}

export function PersonasSection() {
  const { draft, updateForm } = useBuilder();
  const f = draft.form;
  return (
    <>
      <p className="hint">
        Personas are the simulated people in the scenario: who writes to the agent, who it may write to, and who owns the
        mailbox the agent works in. Every persona that sends or receives mail needs a mailbox (next section).
      </p>
      {f.personas.length === 0 && (
        <p className="empty" data-testid="personas-empty">
          No personas yet. Add at least two: the person whose mailbox the agent works in, and someone who writes to them.
        </p>
      )}
      {f.personas.map((p, i) => (
        <PersonaCard key={p.uid} index={i} />
      ))}
      <button
        type="button"
        onClick={() => updateForm((d) => void d.personas.push(blankPersona(d.personas.map((x) => x.id), d.orgDomain)))}
      >
        Add persona
      </button>
    </>
  );
}
