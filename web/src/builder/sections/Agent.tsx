import { useBuilder } from '../context';
import { CheckboxGroup, RadioGroup } from '../fields';
import { DELIVERY_HELP, OPERATION_HELP } from '../help';
import { personaLabel } from '../model';

export function AgentSection({ onGoTo }: { onGoTo: (section: 'mailboxes') => void }) {
  const { draft, updateForm, meta } = useBuilder();
  const f = draft.form;
  const owner = f.personas.find((p) => p.id === f.agentMailbox);
  const ops = meta.agent_operations.map((op) => ({ value: op, label: op, help: OPERATION_HELP[op] }));
  const prohibitable = [...ops, { value: 'external_delivery', label: 'external_delivery', help: OPERATION_HELP.external_delivery }];
  return (
    <>
      <p className="hint">
        What the agent under test may do. Allowed operations are what its credentials permit; prohibited ones are
        refused and recorded as violations, which graders can check. An operation cannot be both.
      </p>
      <p data-testid="agent-mailbox-summary">
        The agent works in{' '}
        {owner ? <strong>{personaLabel(owner)}</strong> : <strong>no mailbox yet</strong>}’s mailbox.{' '}
        <button type="button" className="link" onClick={() => onGoTo('mailboxes')}>
          Change in Mailboxes
        </button>
      </p>
      <CheckboxGroup
        path="scenario.agent.allowed_operations"
        legend="Allowed operations"
        options={ops}
        selected={f.allowed}
        onChange={(v) =>
          updateForm((d) => {
            d.allowed = v;
            d.prohibited = d.prohibited.filter((x) => !v.includes(x));
          })
        }
      />
      <CheckboxGroup
        path="scenario.agent.prohibited_operations"
        legend="Prohibited operations"
        options={prohibitable}
        selected={f.prohibited}
        onChange={(v) =>
          updateForm((d) => {
            d.prohibited = v;
            d.allowed = d.allowed.filter((x) => !v.includes(x));
          })
        }
      />
      <RadioGroup
        path="scenario.environment.delivery.external_delivery"
        legend="Mail to outside addresses"
        help="Nothing ever leaves the simulator. This only decides how such attempts are handled and recorded."
        value={f.externalDelivery}
        options={meta.external_delivery_modes.map((m) => ({ value: m, label: m, help: DELIVERY_HELP[m] }))}
        onChange={(v) => updateForm((d) => void (d.externalDelivery = v))}
      />
      <div className="checkbox">
        <input
          id="fld-scenario-environment-delivery-persona_replies"
          type="checkbox"
          checked={f.personaReplies}
          aria-describedby="persona-replies-help"
          onChange={(e) => updateForm((d) => void (d.personaReplies = e.target.checked))}
        />
        <label htmlFor="fld-scenario-environment-delivery-persona_replies">Personas reply to the agent</label>
      </div>
      <p id="persona-replies-help" className="hint">
        When off, scripted and LLM personas stay silent in every run of this scenario.
      </p>
    </>
  );
}
