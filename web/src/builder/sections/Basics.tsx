import { useBuilder } from '../context';
import { StringListField, TextField } from '../fields';

export function BasicsSection() {
  const { draft, updateForm, idLocked } = useBuilder();
  const f = draft.form;
  return (
    <>
      <p className="hint">
        A scenario is a self-contained mail world: an organisation, the people in it (personas), their mailboxes and
        messages, things that happen over time (events), and the mailbox your agent works in. Publishing stores it as a
        new immutable version that runs are started from.
      </p>
      <TextField
        path="scenario.id"
        label="Scenario id"
        required
        value={f.id}
        readOnly={idLocked}
        placeholder="finance.invoice-review"
        help={
          idLocked
            ? 'This is the key of the scenario you are editing; saving publishes a new version of it. Use Clone to create a different scenario.'
            : 'The scenario key: lowercase letters, digits, dots and dashes, e.g. finance.invoice-review. It cannot change after publishing.'
        }
        onChange={(v) => updateForm((d) => void (d.id = v))}
      />
      <TextField
        path="scenario.title"
        label="Title"
        required
        value={f.title}
        placeholder="Invoice with a suspicious bank-detail change"
        help="A short human-readable name shown in lists."
        onChange={(v) => updateForm((d) => void (d.title = v))}
      />
      <StringListField
        path="scenario.tags"
        label="Tags"
        items={f.tags}
        placeholder="finance"
        help="Optional labels used to filter scenarios. Press Enter or Add after each tag."
        onChange={(v) => updateForm((d) => void (d.tags = v))}
      />
      <TextField
        path="scenario.version"
        label="Version"
        type="number"
        min={1}
        required
        value={f.version}
        readOnly={idLocked}
        help={
          idLocked
            ? 'Set to the next version number automatically.'
            : 'Start at 1. Each publish of the same scenario id needs a higher version.'
        }
        onChange={(v) => updateForm((d) => void (d.version = v))}
      />
    </>
  );
}
