// "Inject email" opened from a mailbox (controller/operator): deliver a message into this run's
// simulated mailbox as if a persona or an outside sender wrote it. Never leaves the sandbox.
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import type { CanonicalMessage, InjectRequest, InjectResult } from '../api/types';
import { useApi } from '../auth/session';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { splitList } from '../components/format';
import { validateInject, type Errors } from '../run/injectValidation';
import { runKeys } from '../run/keys';
import type { BrowserMailbox } from './useRunMailboxes';
import { mailboxTitle } from './useRunMailboxes';

const EXTERNAL = '__external__';

function Recipients({
  legend,
  name,
  mailboxes,
  picked,
  onPick,
  extra,
  onExtra,
  error,
}: {
  legend: string;
  name: string;
  mailboxes: BrowserMailbox[];
  picked: string[];
  onPick: (next: string[]) => void;
  extra: string;
  onExtra: (v: string) => void;
  error?: string;
}) {
  const extraId = `inj2-${name}-extra`;
  return (
    <fieldset className="mb-recipients" id={`inj2-${name}`} tabIndex={-1} aria-describedby={error ? `inj2-${name}-err` : undefined}>
      <legend>{legend}</legend>
      {mailboxes.map((mb) => (
        <label key={mb.id} className="checkbox">
          <input
            type="checkbox"
            checked={picked.includes(mb.email)}
            onChange={(e) => onPick(e.target.checked ? [...picked, mb.email] : picked.filter((x) => x !== mb.email))}
          />
          {mailboxTitle(mb)} &lt;{mb.email}&gt;
        </label>
      ))}
      <div className="field">
        <label htmlFor={extraId}>Other {name === 'to' ? 'recipients' : 'Cc addresses'} (comma-separated)</label>
        <input id={extraId} value={extra} onChange={(e) => onExtra(e.target.value)} />
      </div>
      {error && (
        <p id={`inj2-${name}-err`} className="field-error">
          {error}
        </p>
      )}
    </fieldset>
  );
}

export function ComposeInject({
  runId,
  mailbox,
  mailboxes,
  replyCandidates,
  onClose,
  onOpenMessage,
}: {
  runId: string;
  mailbox: BrowserMailbox;
  mailboxes: BrowserMailbox[];
  /** Messages currently listed (newest first) that the new email can reply to. */
  replyCandidates: CanonicalMessage[];
  onClose: () => void;
  onOpenMessage: (id: string) => void;
}) {
  const api = useApi();
  const qc = useQueryClient();
  const heading = useRef<HTMLHeadingElement>(null);
  const senders = mailboxes.filter((m) => m.id !== mailbox.id);
  const [fromChoice, setFromChoice] = useState(senders[0]?.email ?? EXTERNAL);
  const [external, setExternal] = useState('');
  const [to, setTo] = useState<string[]>([mailbox.email]);
  const [toExtra, setToExtra] = useState('');
  const [cc, setCc] = useState<string[]>([]);
  const [ccExtra, setCcExtra] = useState('');
  const [subject, setSubject] = useState('');
  const [text, setText] = useState('');
  const [html, setHtml] = useState('');
  const [replyTo, setReplyTo] = useState('');
  const [errors, setErrors] = useState<Errors>({});
  const [result, setResult] = useState<InjectResult | null>(null);

  useEffect(() => heading.current?.focus(), []);

  const mutation = useMutation({
    mutationFn: (body: InjectRequest) => api.inject(runId, mailbox.id, body),
    onMutate: () => setResult(null),
    onSuccess: (r) => setResult(r),
    onSettled: () => qc.invalidateQueries({ queryKey: runKeys.all(runId) }),
  });

  const from = fromChoice === EXTERNAL ? external.trim() : fromChoice;
  const allTo = [...to, ...splitList(toExtra)];
  const allCc = [...cc, ...splitList(ccExtra)];

  const pickReply = (id: string) => {
    setReplyTo(id);
    const m = replyCandidates.find((x) => x.id === id);
    if (m && !subject.trim()) setSubject(/^re:/i.test(m.subject) ? m.subject : `Re: ${m.subject}`);
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const errs = validateInject({
      mailbox: mailbox.id,
      from,
      to: allTo.join(','),
      cc: allCc.join(','),
      subject,
      text,
      html,
      eventId: '',
    });
    setErrors(errs);
    if (Object.keys(errs).length > 0) return;
    const parent = replyCandidates.find((m) => m.id === replyTo);
    mutation.mutate({
      message: {
        from,
        to: allTo,
        cc: allCc,
        subject,
        text: text || null,
        html: html || null,
        in_reply_to: parent?.message_id_header ?? null,
        references: parent ? [...(parent.references ?? []), parent.message_id_header] : null,
      },
      delivery: { mode: 'immediate' },
    });
  };

  return (
    <section aria-labelledby="inj2-h" className="mb-compose" data-testid="compose-inject">
      <div className="row space-between">
        <h3 id="inj2-h" tabIndex={-1} ref={heading}>
          Inject an email into {mailboxTitle(mailbox)}’s mailbox
        </h3>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="hint">
        The email is delivered straight into this simulated mailbox ({mailbox.email}), as if the sender had written it. It
        never reaches a real mail server. Use it to add a surprise message mid-run and watch how the agent reacts.
      </p>
      {Object.keys(errors).length > 0 && (
        <div role="alert" className="error-box" data-testid="compose-validation">
          <p className="error-title">Fix {Object.keys(errors).length} field(s):</p>
          <ul>
            {Object.entries(errors).map(([k, msg]) => (
              <li key={k}>{msg}</li>
            ))}
          </ul>
        </div>
      )}
      <form className="form" onSubmit={onSubmit} noValidate aria-label="Inject email">
        <div className="field">
          <label htmlFor="inj2-from">From</label>
          <select id="inj2-from" value={fromChoice} onChange={(e) => setFromChoice(e.target.value)}>
            {senders.map((m) => (
              <option key={m.id} value={m.email}>
                {mailboxTitle(m)} &lt;{m.email}&gt;
              </option>
            ))}
            <option value={EXTERNAL}>Someone outside the scenario…</option>
          </select>
        </div>
        {fromChoice === EXTERNAL && (
          <div className="field">
            <label htmlFor="inj2-external">Sender address</label>
            <input
              id="inj2-external"
              type="email"
              value={external}
              onChange={(e) => setExternal(e.target.value)}
              aria-invalid={!!errors.from}
              aria-describedby={errors.from ? 'inj2-external-err' : undefined}
            />
            {errors.from && (
              <p id="inj2-external-err" className="field-error">
                {errors.from}
              </p>
            )}
          </div>
        )}
        <Recipients legend="To" name="to" mailboxes={mailboxes} picked={to} onPick={setTo} extra={toExtra} onExtra={setToExtra} error={errors.to} />
        <Recipients legend="Cc" name="cc" mailboxes={mailboxes} picked={cc} onPick={setCc} extra={ccExtra} onExtra={setCcExtra} />
        <div className="field">
          <label htmlFor="inj2-reply">In reply to (threading)</label>
          <select id="inj2-reply" value={replyTo} onChange={(e) => pickReply(e.target.value)}>
            <option value="">Not a reply (starts a new thread)</option>
            {replyCandidates.map((m) => (
              <option key={m.id} value={m.id}>
                {m.subject || '(no subject)'} — {m.from.email}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="inj2-subject">Subject</label>
          <input id="inj2-subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="inj2-text">Text body</label>
          <textarea
            id="inj2-text"
            rows={5}
            value={text}
            onChange={(e) => setText(e.target.value)}
            aria-invalid={!!errors.text}
            aria-describedby={errors.text ? 'inj2-text-err' : undefined}
          />
          {errors.text && (
            <p id="inj2-text-err" className="field-error">
              {errors.text}
            </p>
          )}
        </div>
        <div className="field">
          <label htmlFor="inj2-html">HTML body (optional, only ever shown in a sandbox)</label>
          <textarea id="inj2-html" rows={3} value={html} onChange={(e) => setHtml(e.target.value)} />
        </div>
        <div className="row">
          <button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? 'Delivering…' : 'Deliver email'}
          </button>
        </div>
      </form>
      <div role="status" aria-live="polite" data-testid="compose-result">
        {result && (
          <p>
            Delivered to {mailbox.email}.{' '}
            <button type="button" className="link" onClick={() => onOpenMessage(result.message_id)}>
              Open the delivered email
            </button>
          </p>
        )}
      </div>
      {mutation.isError && <ErrorDisplay error={mutation.error} title="The email was not delivered" />}
    </section>
  );
}
