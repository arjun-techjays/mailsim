import { useBuilder } from '../context';
import { Group, RadioGroup } from '../fields';
import { personaLabel, personasNeedingMailbox, uid, type ScenarioForm } from '../model';

function ensureMailbox(d: ScenarioForm, owner: string): void {
  if (!d.mailboxes.some((m) => m.owner === owner)) d.mailboxes.push({ uid: uid(), owner, messages: [] });
}

export function MailboxesSection() {
  const { draft, updateForm } = useBuilder();
  const f = draft.form;
  const owners = new Set(f.mailboxes.map((m) => m.owner));
  const personaIds = new Set(f.personas.map((p) => p.id));
  const missing = personasNeedingMailbox(f);
  const orphans = f.mailboxes.map((m, i) => ({ m, i })).filter(({ m }) => !personaIds.has(m.owner));
  const allHave = f.personas.length > 0 && f.personas.every((p) => owners.has(p.id));
  return (
    <>
      <p className="hint">
        Each persona that sends or receives mail needs a mailbox in the simulator. One of them is the mailbox of the
        agent under test: the agent connects to that mailbox only, and everything it reads, sends or files happens
        there.
      </p>
      {f.personas.length === 0 ? (
        <p className="empty">Add personas first (Personas section); then create a mailbox for each of them here.</p>
      ) : (
        <>
          <div className="row">
            <button
              type="button"
              disabled={allHave}
              onClick={() =>
                updateForm((d) => {
                  for (const p of d.personas) ensureMailbox(d, p.id);
                })
              }
            >
              Create mailboxes for all personas
            </button>
            {allHave && <span className="muted">Every persona has a mailbox.</span>}
          </div>
          {missing.length > 0 && (
            <p className="notice" role="note" data-testid="missing-mailboxes">
              These personas receive mail (or are the agent’s) but have no mailbox: {missing.join(', ')}.
            </p>
          )}
          <Group path="scenario.mailboxes" legend="Mailboxes" className="b-checks">
            {f.personas.map((p) => (
              <div className="checkbox" key={p.uid}>
                <input
                  id={`mbx-${p.uid}`}
                  type="checkbox"
                  checked={owners.has(p.id)}
                  onChange={(e) =>
                    updateForm((d) => {
                      if (e.target.checked) ensureMailbox(d, p.id);
                      else {
                        d.mailboxes = d.mailboxes.filter((m) => m.owner !== p.id);
                        if (d.agentMailbox === p.id) d.agentMailbox = '';
                      }
                    })
                  }
                />
                <label htmlFor={`mbx-${p.uid}`}>Mailbox for {personaLabel(p)}</label>
              </div>
            ))}
          </Group>
          <RadioGroup
            path="scenario.agent.mailbox"
            legend="Agent under test uses this mailbox"
            help="Choosing a persona without a mailbox creates one."
            value={f.agentMailbox}
            options={f.personas.map((p) => ({ value: p.id, label: personaLabel(p) }))}
            onChange={(v) =>
              updateForm((d) => {
                d.agentMailbox = v;
                ensureMailbox(d, v);
              })
            }
          />
        </>
      )}
      {orphans.length > 0 && (
        <div className="notice" role="note">
          <p>Mailboxes whose owner is not a persona:</p>
          <ul>
            {orphans.map(({ m, i }) => (
              <li key={m.uid} id={`fld-scenario-mailboxes-${i}-owner`} tabIndex={-1}>
                <code>{m.owner || '(empty)'}</code>{' '}
                <button type="button" onClick={() => updateForm((d) => void d.mailboxes.splice(i, 1))}>
                  Remove mailbox {m.owner || '(empty)'}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
