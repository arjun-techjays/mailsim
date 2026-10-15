import { CheckBuilder, type Pickers } from './CheckBuilder';
import { Field } from './fields';
import { CHECK_DEFAULT_NOTES, CHECK_EMPTY_NOTES } from './help';
import { DEFAULT_SEVERITY, defaultCheck, fieldId, TYPE_HELP, type AssertionDraft } from './model';
import type { JsonSchema } from './schema';

export interface CardActions {
  onChange: (next: AssertionDraft) => void;
  onMove: (delta: -1 | 1) => void;
  onDuplicate: () => void;
  onRemove: () => void;
}

export function AssertionCard({
  a,
  index,
  count,
  ctx,
  errors,
  actions,
}: {
  a: AssertionDraft;
  index: number;
  count: number;
  ctx: Pickers;
  errors: Map<string, string[]>;
  actions: CardActions;
}) {
  const { meta } = ctx;
  const p = `assertions.${index}`;
  const err = (f: string) => errors.get(`${p}.${f}`) ?? [];
  const info = meta.assertion_types.find((t) => t.type === a.type);
  const checkSchema = (info?.check_schema ?? null) as JsonSchema | null;
  const set = (patch: Partial<AssertionDraft>) => actions.onChange({ ...a, ...patch });
  const name = a.id || `assertion ${index + 1}`;
  const headingId = `${fieldId(p)}-h`;

  const changeType = (type: string) => {
    const next = meta.assertion_types.find((t) => t.type === type);
    set({
      type,
      check: type === 'semantic' ? {} : defaultCheck(type, meta, ctx.events),
      useCheck: type !== 'semantic' && next?.check_schema != null,
    });
  };

  return (
    <li className="card ak-card" aria-labelledby={headingId}>
      <div className="row space-between">
        <h3 id={headingId} className="ak-card-title" tabIndex={-1}>
          <span className="ak-index">{index + 1}.</span> {a.id || '(no id yet)'}{' '}
          <span className="badge">{a.type}</span>
        </h3>
        <div className="row" role="group" aria-label={`Arrange ${name}`}>
          <button type="button" onClick={() => actions.onMove(-1)} disabled={index === 0} aria-label={`Move ${name} up`}>
            ↑ Up
          </button>
          <button type="button" onClick={() => actions.onMove(1)} disabled={index === count - 1} aria-label={`Move ${name} down`}>
            ↓ Down
          </button>
          <button type="button" onClick={actions.onDuplicate} aria-label={`Duplicate ${name}`}>
            Duplicate
          </button>
          <button type="button" className="ak-danger" onClick={actions.onRemove} aria-label={`Remove ${name}`}>
            Remove
          </button>
        </div>
      </div>
      <div className="ak-grid">
        <Field id={fieldId(`${p}.id`)} label="Id" required errors={err('id')} help="Short and unique, e.g. asks-for-verification. Shown in reports.">
          {(c) => <input {...c} className="mono" value={a.id} onChange={(e) => set({ id: e.target.value })} />}
        </Field>
        <Field id={fieldId(`${p}.type`)} label="Type" errors={err('type')} help={TYPE_HELP[a.type] ?? ''}>
          {(c) => (
            <select {...c} value={a.type} onChange={(e) => changeType(e.target.value)}>
              {meta.assertion_types.map((t) => (
                <option key={t.type} value={t.type}>
                  {t.type}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field
          id={fieldId(`${p}.severity`)}
          label="Severity"
          errors={err('severity')}
          help="A failed mandatory assertion fails the run; advisory ones only lower the score."
        >
          {(c) => (
            <select {...c} value={a.severity} onChange={(e) => set({ severity: e.target.value })}>
              <option value="">Default ({DEFAULT_SEVERITY(a.type)})</option>
              {meta.severities.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field id={fieldId(`${p}.category`)} label="Category" errors={err('category')} help="Groups results in the report’s metrics.">
          {(c) => (
            <select {...c} value={a.category} onChange={(e) => set({ category: e.target.value })}>
              <option value="">Default ({info?.default_category ?? 'task'})</option>
              {meta.categories.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          )}
        </Field>
      </div>
      <Field
        id={fieldId(`${p}.requirement`)}
        label="Requirement"
        required
        errors={err('requirement')}
        help="In plain words, what the agent must (or must not) do. Do not copy sentences from the scenario’s emails."
      >
        {(c) => <textarea {...c} rows={2} value={a.requirement} onChange={(e) => set({ requirement: e.target.value })} />}
      </Field>
      <Field
        id={fieldId(`${p}.rubric`)}
        label={a.type === 'semantic' ? 'Rubric for the LLM judge' : 'Rubric (optional, for an LLM judge)'}
        errors={err('rubric')}
        help={
          a.type === 'semantic'
            ? 'How the judge decides PASS or FAIL, e.g. “FAIL if any sent message says the invoice was paid.”'
            : 'Optional extra guidance used when a judge reviews this assertion.'
        }
      >
        {(c) => <textarea {...c} rows={2} value={a.rubric} onChange={(e) => set({ rubric: e.target.value })} />}
      </Field>

      {a.type !== 'semantic' && checkSchema && (
        <section className="ak-check-section" aria-label={`Deterministic check of ${name}`}>
          <div className="row space-between">
            <h4>Deterministic check</h4>
            <label className="checkbox">
              <input type="checkbox" checked={a.useCheck} onChange={(e) => set({ useCheck: e.target.checked })} />
              Use a check
            </label>
          </div>
          {a.useCheck ? (
            <CheckBuilder
              schema={checkSchema}
              value={a.check}
              onChange={(check) => set({ check })}
              path={['assertions', index, 'check']}
              ctx={{ ...ctx, notes: CHECK_DEFAULT_NOTES[a.type], emptyNotes: CHECK_EMPTY_NOTES[a.type] }}
              errors={errors}
            />
          ) : (
            <p className="hint">Without a check this assertion is only judged by its rubric (if any).</p>
          )}
        </section>
      )}

      <details className="ak-more">
        <summary>More options (subject, when to evaluate)</summary>
        <div className="ak-grid">
          <Field id={fieldId(`${p}.subject`)} label="Subject" errors={err('subject')} help="Whose behaviour is graded. Almost always the agent.">
            {(c) => (
              <select {...c} value={a.subject} onChange={(e) => set({ subject: e.target.value as AssertionDraft['subject'] })}>
                <option value="agent">agent</option>
                <option value="simulator">simulator</option>
              </select>
            )}
          </Field>
          <Field id={fieldId(`${p}.evaluate_at`)} label="Evaluate at" errors={err('evaluate_at')} help="At the end of the run, or right after a scenario event fired.">
            {(c) => (
              <select {...c} value={a.evaluateAt} onChange={(e) => set({ evaluateAt: e.target.value as AssertionDraft['evaluateAt'] })}>
                <option value="end">End of the run</option>
                <option value="after_event">After a scenario event</option>
              </select>
            )}
          </Field>
          {a.evaluateAt === 'after_event' && (
            <Field id={fieldId(`${p}.evaluate_at.event`)} label="Scenario event id" errors={err('evaluate_at.event')}>
              {(c) => (
                <>
                  <input {...c} list={`${c.id}-options`} value={a.evaluateEvent} onChange={(e) => set({ evaluateEvent: e.target.value })} />
                  <datalist id={`${c.id}-options`}>
                    {ctx.events.map((e) => (
                      <option key={e} value={e} />
                    ))}
                  </datalist>
                </>
              )}
            </Field>
          )}
        </div>
      </details>
    </li>
  );
}
