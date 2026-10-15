import { useBuilder } from '../context';
import { Group, TextArea, TextField } from '../fields';
import { defaultEmail, uid, uniqueId } from '../model';

export function OrganisationSection() {
  const { draft, updateForm } = useBuilder();
  const f = draft.form;
  return (
    <>
      <p className="hint">
        The organisation the scenario takes place in. Its mail domain is used for persona addresses; policies are rules
        the agent is expected to follow (they are part of the scenario, visible to graders and persona models).
      </p>
      <TextField
        path="scenario.organization.domain"
        label="Mail domain"
        required
        value={f.orgDomain}
        placeholder="example.test"
        help="Use a reserved domain such as example.test. Persona addresses default to <persona id>@<domain>."
        onChange={(v) =>
          updateForm((d) => {
            d.orgDomain = v;
            for (const p of d.personas) if (!p.emailTouched) p.email = defaultEmail(p.id, v);
          })
        }
      />
      <TextField
        path="scenario.organization.name"
        label="Organisation name (optional)"
        value={f.orgName}
        placeholder="Example Ltd"
        onChange={(v) => updateForm((d) => void (d.orgName = v))}
      />
      <Group path="scenario.organization.policies" legend="Policies" help="Optional. Each policy has a short id and the rule in plain words.">
        {f.policies.length === 0 && <p className="muted">No policies. Add one if the agent must follow a business rule.</p>}
        {f.policies.map((p, i) => (
          <div className="b-item" key={p.uid}>
            <TextField
              path={`scenario.organization.policies.${i}.id`}
              label={`Policy ${i + 1} id`}
              value={p.id}
              onChange={(v) => updateForm((d) => void ((d.policies[i] as typeof p).id = v))}
            />
            <TextArea
              path={`scenario.organization.policies.${i}.rule`}
              label={`Policy ${i + 1} rule`}
              rows={2}
              value={p.rule}
              onChange={(v) => updateForm((d) => void ((d.policies[i] as typeof p).rule = v))}
            />
            <button type="button" onClick={() => updateForm((d) => void d.policies.splice(i, 1))}>
              Remove policy {i + 1}
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() =>
            updateForm((d) =>
              void d.policies.push({ uid: uid(), id: uniqueId('policy', d.policies.map((x) => x.id), '-'), rule: '' }),
            )
          }
        >
          Add policy
        </button>
      </Group>
    </>
  );
}
