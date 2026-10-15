import { useBuilder } from '../context';
import { CheckboxGroup, Group, RadioGroup, SelectField, TextArea, TextField } from '../fields';
import { TRIGGER_HELP } from '../help';
import { blankEvent, personaLabel, type EventForm } from '../model';
import { fromIsoInput, toIsoInput } from '../time';

function EventCard({ index }: { index: number }) {
  const { draft, updateForm, meta } = useBuilder();
  const f = draft.form;
  const e = f.events[index] as EventForm;
  const base = `scenario.events.${index}`;
  const edit = (fn: (x: EventForm) => void) => updateForm((d) => fn(d.events[index] as EventForm));
  const people = f.personas.map((p) => ({ value: p.id, label: personaLabel(p) }));
  const n = index + 1;
  return (
    <Group path={base} legend={`Event ${n}: ${e.id || 'unnamed'}`} className="b-card">
      <TextField path={`${base}.id`} label="Event id" required value={e.id} help="A short unique name, e.g. vendor-follow-up." onChange={(v) => edit((x) => void (x.id = v))} />
      <RadioGroup
        path={`${base}.trigger.type`}
        legend="When does it happen?"
        value={e.triggerType}
        options={meta.trigger_types.map((t) => ({ value: t, label: t, help: TRIGGER_HELP[t] }))}
        onChange={(v) => edit((x) => void (x.triggerType = v))}
      />
      {e.triggerType === 'at' && (
        <TextField
          path={`${base}.trigger.at`}
          label="At (UTC)"
          type="datetime-local"
          value={toIsoInput(e.at)}
          help={e.at && !toIsoInput(e.at) ? `Current value ${e.at} cannot be shown here; pick a new time or edit it in YAML.` : 'Must not be before the clock start.'}
          onChange={(v) => edit((x) => void (x.at = fromIsoInput(v)))}
        />
      )}
      {e.triggerType === 'after_seconds' && (
        <TextField
          path={`${base}.trigger.seconds`}
          label="Seconds after the run starts"
          type="number"
          min={0}
          value={e.seconds}
          help="Virtual seconds: 600 is ten minutes. Advance the run's clock to make it happen."
          onChange={(v) => edit((x) => void (x.seconds = v))}
        />
      )}
      {e.triggerType === 'after_agent_action' && (
        <SelectField
          path={`${base}.trigger.action`}
          label="After the agent’s first…"
          value={e.action}
          placeholder="Choose an action"
          options={meta.trigger_actions.map((a) => ({ value: a, label: a }))}
          onChange={(v) => edit((x) => void (x.action = v))}
        />
      )}
      <TextField
        path={`${base}.trigger.delay_seconds`}
        label="Extra delay (seconds, optional)"
        type="number"
        min={0}
        value={e.delay_seconds}
        placeholder="0"
        onChange={(v) => edit((x) => void (x.delay_seconds = v))}
      />
      <SelectField
        path={`${base}.event.from`}
        label="From"
        value={e.from}
        placeholder="Choose a persona"
        options={people}
        onChange={(v) => edit((x) => void (x.from = v))}
      />
      <CheckboxGroup
        path={`${base}.event.to`}
        legend="To"
        help="Recipients must be personas with a mailbox."
        options={people}
        selected={e.to}
        onChange={(v) => edit((x) => void (x.to = v))}
      />
      <TextField path={`${base}.event.subject`} label="Subject" required value={e.subject} onChange={(v) => edit((x) => void (x.subject = v))} />
      <TextArea path={`${base}.event.body`} label="Body" value={e.body} onChange={(v) => edit((x) => void (x.body = v))} />
      <button type="button" onClick={() => updateForm((d) => void d.events.splice(index, 1))}>
        Remove event {n}
      </button>
    </Group>
  );
}

export function EventsSection() {
  const { draft, updateForm } = useBuilder();
  const f = draft.form;
  return (
    <>
      <p className="hint">
        Events are emails that arrive while the run is going: at a set time, some time after the start, or in response
        to something the agent does (a trigger). They are how a scenario changes over time — a follow-up, an urgent
        request, a suspicious change.
      </p>
      {f.events.length === 0 && <p className="empty">No events: nothing new arrives during a run. Add one to make the scenario change over time.</p>}
      {f.events.map((e, i) => (
        <EventCard key={e.uid} index={i} />
      ))}
      <button
        type="button"
        onClick={() =>
          updateForm((d) => {
            const sender = d.personas.find((p) => p.id !== d.agentMailbox)?.id ?? '';
            d.events.push(blankEvent(d.events.map((x) => x.id), sender, d.agentMailbox ? [d.agentMailbox] : []));
          })
        }
      >
        Add event
      </button>
    </>
  );
}
