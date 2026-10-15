import { useState, type FormEvent } from 'react';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { splitList } from '../components/format';
import type { SnapshotMailbox } from '../mailbox/snapshot';
import { useInjectMutation } from './simActions';

import { validateInject, type Errors, type Fields } from './injectValidation';

const LABELS: Record<keyof Fields, string> = {
  mailbox: 'Deliver to mailbox',
  from: 'From',
  to: 'To (comma-separated)',
  cc: 'Cc (comma-separated)',
  subject: 'Subject',
  text: 'Text body',
  html: 'HTML body (optional, rendered only in a sandbox)',
  eventId: 'Event id (optional)',
};

export function InjectForm({ runId, mailboxes }: { runId: string; mailboxes: SnapshotMailbox[] }) {
  const [f, setF] = useState<Fields>({
    mailbox: mailboxes[0]?.id ?? '',
    from: '',
    to: mailboxes[0]?.email ?? '',
    cc: '',
    subject: '',
    text: '',
    html: '',
    eventId: '',
  });
  const [errors, setErrors] = useState<Errors>({});
  const { mutation, result } = useInjectMutation(runId);

  const set = (k: keyof Fields) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const errs = validateInject(f);
    setErrors(errs);
    if (Object.keys(errs).length > 0) return;
    mutation.mutate({
      mailboxId: f.mailbox,
      body: {
      event_id: f.eventId.trim() || null,
      message: {
        from: f.from.trim(),
        to: splitList(f.to),
        cc: splitList(f.cc),
        subject: f.subject,
        text: f.text || null,
        html: f.html || null,
      },
      delivery: { mode: 'immediate' },
      },
    });
  };

  const errorList = Object.entries(errors) as [keyof Fields, string][];
  const describe = (k: keyof Fields) => (errors[k] ? `inj-${k}-err` : undefined);
  const textField = (k: keyof Fields, type = 'text') => (
    <div className="field" key={k}>
      <label htmlFor={`inj-${k}`}>{LABELS[k]}</label>
      <input
        id={`inj-${k}`}
        type={type}
        value={f[k]}
        onChange={set(k)}
        aria-invalid={!!errors[k]}
        aria-describedby={describe(k)}
      />
      {errors[k] && (
        <p id={`inj-${k}-err`} className="field-error">
          {errors[k]}
        </p>
      )}
    </div>
  );

  return (
    <section aria-labelledby="inject-h" className="card">
      <h2 id="inject-h">Inject inbound message</h2>
      <p className="hint">Delivered to a simulated mailbox only. Nothing ever reaches a real recipient.</p>
      {errorList.length > 0 && (
        <div role="alert" className="error-box" data-testid="inject-validation">
          <p className="error-title">Fix {errorList.length} field(s):</p>
          <ul>
            {errorList.map(([k, msg]) => (
              <li key={k}>
                <a href={`#inj-${k}`}>{LABELS[k]}</a>: {msg}
              </li>
            ))}
          </ul>
        </div>
      )}
      <form className="form" onSubmit={onSubmit} noValidate aria-label="Inject message">
        <div className="field">
          <label htmlFor="inj-mailbox">{LABELS.mailbox}</label>
          {mailboxes.length > 0 ? (
            <select
              id="inj-mailbox"
              value={f.mailbox}
              onChange={(e) => {
                // "To" follows the selected mailbox so the message lands where it is addressed.
                const mb = mailboxes.find((m) => m.id === e.target.value) as SnapshotMailbox;
                setF({ ...f, mailbox: mb.id, to: mb.email });
              }}
            >
              {mailboxes.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.email} ({m.id})
                </option>
              ))}
            </select>
          ) : (
            <input
              id="inj-mailbox"
              value={f.mailbox}
              onChange={set('mailbox')}
              aria-invalid={!!errors.mailbox}
              aria-describedby={describe('mailbox')}
            />
          )}
          {errors.mailbox && (
            <p id="inj-mailbox-err" className="field-error">
              {errors.mailbox}
            </p>
          )}
        </div>
        {textField('from', 'email')}
        {textField('to')}
        {textField('cc')}
        {textField('subject')}
        <div className="field">
          <label htmlFor="inj-text">{LABELS.text}</label>
          <textarea
            id="inj-text"
            rows={4}
            value={f.text}
            onChange={set('text')}
            aria-invalid={!!errors.text}
            aria-describedby={describe('text')}
          />
          {errors.text && (
            <p id="inj-text-err" className="field-error">
              {errors.text}
            </p>
          )}
        </div>
        <div className="field">
          <label htmlFor="inj-html">{LABELS.html}</label>
          <textarea id="inj-html" rows={3} value={f.html} onChange={set('html')} />
        </div>
        {textField('eventId')}
        <button type="submit" disabled={mutation.isPending}>
          {mutation.isPending ? 'Injecting…' : 'Inject message'}
        </button>
      </form>
      <div role="status" aria-live="polite" data-testid="inject-result">
        {result && (
          <p>
            Delivered to simulated mailbox: message <code>{result.message_id}</code> (event <code>{result.event_id}</code>
            ).
          </p>
        )}
      </div>
      {mutation.isError && <ErrorDisplay error={mutation.error} title="Injection rejected" />}
    </section>
  );
}
