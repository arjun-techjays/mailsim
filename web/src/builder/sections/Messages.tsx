import { useId, useState, type ChangeEvent, type FormEvent } from 'react';
import type { ComposeMessage } from '../../api/types';
import { ErrorDisplay } from '../../components/ErrorDisplay';
import { SafeEmailBody } from '../../components/SafeEmailBody';
import { useBuilder, useIssues } from '../context';
import type { FixtureInfo } from '../draft';
import { CheckboxGroup, SelectField, TextArea, TextField } from '../fields';
import { base64Size, fileToBase64, formatBytes, MAX_FILE_BYTES, uniqueFixturePath } from '../files';
import { personaLabel, slugId, type MailboxForm, type PersonaForm } from '../model';
import { fieldId } from '../paths';
import { fromIsoInput } from '../time';

interface Attachment {
  filename: string;
  content_type: string;
  content_b64: string;
  size: number;
}

const splitAddresses = (text: string) =>
  text
    .split(/[,;\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);

function MessageItem({ mailboxIndex, index, path, owner }: { mailboxIndex: number; index: number; path: string; owner: string }) {
  const { draft, removeMessage } = useBuilder();
  const base = `scenario.mailboxes.${mailboxIndex}.initial_messages.${index}.fixture`;
  const issues = useIssues(base);
  const info = draft.info[path];
  const size = draft.fixtures[path] !== undefined ? base64Size(draft.fixtures[path] as string) : null;
  return (
    <li id={fieldId(base)} tabIndex={-1} className="b-message" data-testid="initial-message">
      <p>
        <strong>{info ? info.subject || '(no subject)' : path}</strong>
        {info?.source === 'uploaded' && <span className="badge b-badge-note">uploaded .eml</span>}
      </p>
      {info ? (
        <dl className="kv">
          <dt>From</dt>
          <dd>{info.from ?? '—'}</dd>
          <dt>To</dt>
          <dd>{info.to.join(', ') || '—'}</dd>
          {info.in_reply_to && (
            <>
              <dt>In reply to</dt>
              <dd>
                <code>{info.in_reply_to}</code>
              </dd>
            </>
          )}
          <dt>Message-ID</dt>
          <dd>
            <code>{info.message_id ?? '—'}</code>
          </dd>
          {info.attachments.length > 0 && (
            <>
              <dt>Attachments</dt>
              <dd>{info.attachments.join(', ')}</dd>
            </>
          )}
          <dt>File</dt>
          <dd>
            <code>{path}</code>
            {size !== null && ` (${formatBytes(size)})`}
          </dd>
        </dl>
      ) : (
        <p className="muted">{size === null ? 'File missing: upload it again or remove this message.' : 'Reading message…'}</p>
      )}
      {issues.length > 0 && (
        <p className="field-error" data-testid="inline-error">
          {issues.join('; ')}
        </p>
      )}
      <button type="button" onClick={() => removeMessage(owner, path)}>
        Remove message {index + 1}: “{info?.subject || path}”
      </button>
    </li>
  );
}

function ComposeForm({ owner, onClose }: { owner: string; onClose: () => void }) {
  const { api, draft, addFixture } = useBuilder();
  const personas = draft.form.personas;
  const ownerPersona = personas.find((p) => p.id === owner);
  const firstOther = personas.find((p) => p.id !== owner);
  const formId = useId();
  const [fromKind, setFromKind] = useState<'persona' | 'external'>(firstOther ? 'persona' : 'external');
  const [fromPersona, setFromPersona] = useState(firstOther?.email ?? '');
  const [fromExternal, setFromExternal] = useState('');
  const [to, setTo] = useState<string[]>(ownerPersona?.email ? [ownerPersona.email] : []);
  const [toOther, setToOther] = useState('');
  const [cc, setCc] = useState<string[]>([]);
  const [ccOther, setCcOther] = useState('');
  const [subject, setSubject] = useState('');
  const [text, setText] = useState('');
  const [html, setHtml] = useState('');
  const [preview, setPreview] = useState(false);
  const [date, setDate] = useState('');
  const [replyTo, setReplyTo] = useState('');
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const p = `compose.${owner}`;
  const options = personas.filter((x) => x.email).map((x: PersonaForm) => ({ value: x.email, label: personaLabel(x) }));
  const earlier = Object.entries(draft.info).filter(([, i]) => i.message_id);

  const onFiles = async (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files as FileList);
    e.target.value = '';
    const added: Attachment[] = [];
    for (const file of files) {
      if (file.size > MAX_FILE_BYTES) {
        setProblem(`${file.name} is larger than ${formatBytes(MAX_FILE_BYTES)}.`);
        continue;
      }
      added.push({
        filename: file.name,
        content_type: file.type || 'application/octet-stream',
        content_b64: await fileToBase64(file),
        size: file.size,
      });
    }
    setAttachments((a) => [...a, ...added]);
  };

  const onReplyTo = (path: string) => {
    setReplyTo(path);
    const prev = draft.info[path];
    if (prev && !subject) setSubject(/^re:/i.test(prev.subject) ? prev.subject : `Re: ${prev.subject}`);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const from = fromKind === 'persona' ? fromPersona : fromExternal.trim();
    const toAll = [...to, ...splitAddresses(toOther)];
    if (!from) return setProblem('Choose who the message is from.');
    if (toAll.length === 0) return setProblem('Add at least one recipient.');
    if (!text.trim() && !html.trim()) return setProblem('Write a text or HTML body.');
    setProblem(null);
    setError(null);
    const parent = replyTo ? draft.info[replyTo] : undefined;
    const inReplyTo = parent?.message_id ?? null;
    const body: ComposeMessage = {
      from,
      to: toAll,
      cc: [...cc, ...splitAddresses(ccOther)],
      subject,
      text: text || null,
      html: html || null,
      date: fromIsoInput(date) || null,
      in_reply_to: inReplyTo,
      references: parent && inReplyTo ? [...(parent.in_reply_to ? [parent.in_reply_to] : []), inReplyTo] : [],
      attachments: attachments.map(({ filename, content_type, content_b64 }) => ({ filename, content_type, content_b64 })),
      fixture_path: uniqueFixturePath(`${owner}-${slugId(subject) || 'message'}.eml`, Object.keys(draft.fixtures)),
    };
    setBusy(true);
    try {
      const res = await api.builder.composeEml(body);
      const info: FixtureInfo = {
        source: 'composed',
        message_id: res.message_id,
        in_reply_to: inReplyTo,
        from,
        to: toAll,
        subject,
        attachments: attachments.map((a) => a.filename),
        text_preview: text.slice(0, 200),
      };
      addFixture(res.path, res.content_b64, info, owner);
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="b-compose" aria-labelledby={`${formId}-h`} onSubmit={(e) => void submit(e)} noValidate>
      <h4 id={`${formId}-h`}>New message in {ownerPersona ? personaLabel(ownerPersona) : owner}’s mailbox</h4>
      <fieldset>
        <legend>From</legend>
        <div className="radio">
          <input id={`${formId}-fp`} type="radio" name={`${formId}-from`} checked={fromKind === 'persona'} disabled={options.length === 0} onChange={() => setFromKind('persona')} />
          <label htmlFor={`${formId}-fp`}>A persona</label>
        </div>
        <div className="radio">
          <input id={`${formId}-fe`} type="radio" name={`${formId}-from`} checked={fromKind === 'external'} onChange={() => setFromKind('external')} />
          <label htmlFor={`${formId}-fe`}>An outside address</label>
        </div>
        {fromKind === 'persona' ? (
          <SelectField path={`${p}.from`} label="Sender persona" value={fromPersona} options={options} placeholder="Choose a persona" onChange={setFromPersona} />
        ) : (
          <TextField path={`${p}.from-external`} label="Sender address" type="email" value={fromExternal} placeholder="someone@outside.test" onChange={setFromExternal} />
        )}
      </fieldset>
      <CheckboxGroup path={`${p}.to`} legend="To" options={options} selected={to} onChange={setTo} />
      <TextField path={`${p}.to-other`} label="Other To addresses (comma separated)" value={toOther} onChange={setToOther} />
      <CheckboxGroup path={`${p}.cc`} legend="Cc" options={options} selected={cc} onChange={setCc} />
      <TextField path={`${p}.cc-other`} label="Other Cc addresses (comma separated)" value={ccOther} onChange={setCcOther} />
      {earlier.length > 0 && (
        <SelectField
          path={`${p}.reply-to`}
          label="Reply to an earlier message (threading)"
          value={replyTo}
          placeholder="Not a reply"
          help="Sets In-Reply-To and References so mail clients group the two messages into one thread."
          options={earlier.map(([path, i]) => ({ value: path, label: `${i.subject || '(no subject)'} — from ${i.from ?? 'unknown'}` }))}
          onChange={onReplyTo}
        />
      )}
      <TextField path={`${p}.subject`} label="Subject" value={subject} onChange={setSubject} />
      <TextArea path={`${p}.text`} label="Plain-text body" rows={6} value={text} onChange={setText} />
      <TextArea
        path={`${p}.html`}
        label="HTML body (optional)"
        rows={4}
        value={html}
        help="Optional. Previews are shown in a sandbox with scripts and remote content blocked."
        onChange={setHtml}
      />
      {html.trim() && (
        <div>
          <button type="button" aria-pressed={preview} onClick={() => setPreview(!preview)}>
            {preview ? 'Hide HTML preview' : 'Preview HTML'}
          </button>
          {preview && <SafeEmailBody text={null} html={html} />}
        </div>
      )}
      <TextField
        path={`${p}.date`}
        label="Date (UTC, optional)"
        type="datetime-local"
        value={date}
        help="Defaults to the scenario's clock start."
        onChange={setDate}
      />
      <div className="field">
        <label htmlFor={`${formId}-att`}>Attachments</label>
        <input id={`${formId}-att`} type="file" multiple onChange={(e) => void onFiles(e)} aria-describedby={`${formId}-att-h`} />
        <span id={`${formId}-att-h`} className="hint">
          Up to {formatBytes(MAX_FILE_BYTES)} each. Files are read in your browser and sent with the message.
        </span>
        {attachments.length > 0 && (
          <ul aria-label="Attached files">
            {attachments.map((a, i) => (
              <li key={`${a.filename}-${i}`}>
                {a.filename} ({formatBytes(a.size)}){' '}
                <button type="button" onClick={() => setAttachments(attachments.filter((_, j) => j !== i))}>
                  Remove {a.filename}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {problem && (
        <p className="field-error" role="alert">
          {problem}
        </p>
      )}
      {error !== null && <ErrorDisplay error={error} title="Could not build the message" />}
      <div className="row">
        <button type="submit" disabled={busy}>
          {busy ? 'Adding…' : 'Add message'}
        </button>
        <button type="button" onClick={onClose}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function MailboxMessages({ mailboxIndex }: { mailboxIndex: number }) {
  const { api, draft, addFixture } = useBuilder();
  const mailbox = draft.form.mailboxes[mailboxIndex] as MailboxForm;
  const persona = draft.form.personas.find((p) => p.id === mailbox.owner);
  const [composing, setComposing] = useState(false);
  const [uploadError, setUploadError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const inputId = useId();
  const who = persona ? personaLabel(persona) : mailbox.owner;

  const onUpload = async (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files as FileList);
    e.target.value = '';
    setUploadError(null);
    setBusy(true);
    const taken = Object.keys(draft.fixtures);
    try {
      for (const file of files) {
        if (file.size > MAX_FILE_BYTES) throw new Error(`${file.name} is larger than ${formatBytes(MAX_FILE_BYTES)}.`);
        const b64 = await fileToBase64(file);
        const summary = await api.builder.inspectEml(b64);
        const path = uniqueFixturePath(file.name, taken);
        taken.push(path);
        addFixture(path, b64, { ...summary, source: 'uploaded' }, mailbox.owner);
      }
    } catch (err) {
      setUploadError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="b-card" aria-labelledby={`${inputId}-h`}>
      <h3 id={`${inputId}-h`}>Mailbox of {who}</h3>
      {mailbox.messages.length === 0 ? (
        <p className="muted">Empty: the mailbox starts with no messages.</p>
      ) : (
        <ol className="b-messages">
          {mailbox.messages.map((path, j) => (
            <MessageItem key={path} mailboxIndex={mailboxIndex} index={j} path={path} owner={mailbox.owner} />
          ))}
        </ol>
      )}
      {composing ? (
        <ComposeForm owner={mailbox.owner} onClose={() => setComposing(false)} />
      ) : (
        <div className="row">
          <button type="button" onClick={() => setComposing(true)}>
            Compose a message for {who}
          </button>
          <label htmlFor={inputId}>Upload .eml files for {who}</label>
          <input
            id={inputId}
            type="file"
            accept=".eml,message/rfc822"
            multiple
            disabled={busy}
            onChange={(e) => void onUpload(e)}
          />
          {busy && <span role="status">Reading files…</span>}
        </div>
      )}
      {uploadError !== null && <ErrorDisplay error={uploadError} title="Could not add the .eml file" />}
    </section>
  );
}

export function MessagesSection() {
  const { draft } = useBuilder();
  const mailboxes = draft.form.mailboxes;
  return (
    <>
      <p className="hint">
        Messages already in each mailbox when a run starts. Compose them here (the server builds a standard .eml file,
        with attachments and threading headers) or upload existing .eml files. To make a thread, compose a reply to an
        earlier message.
      </p>
      {mailboxes.length === 0 ? (
        <p className="empty">No mailboxes yet. Create them in the Mailboxes section first.</p>
      ) : (
        mailboxes.map((m, i) => <MailboxMessages key={m.uid} mailboxIndex={i} />)
      )}
    </>
  );
}
