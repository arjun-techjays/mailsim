import { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import type { AttachmentRef, CanonicalMessage } from '../api/types';
import { saveBlob } from '../components/download';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { address, addresses, bytes } from '../components/format';
import { SafeEmailBody } from '../components/SafeEmailBody';
import { snippet, subjectOf } from './mailFormat';
import { VirtualTime } from './VirtualTime';


function Headers({ m, rawMimeHref }: { m: CanonicalMessage; rawMimeHref?: (ref: string) => string }) {
  const headersId = useId();
  return (
    <div className="mb-headers" data-testid="message-headers">
      <dl className="kv">
        <dt>Message-ID</dt>
        <dd>
          <code>{m.message_id_header}</code>
        </dd>
        {m.in_reply_to && (
          <>
            <dt>In-Reply-To</dt>
            <dd>
              <code>{m.in_reply_to}</code>
            </dd>
          </>
        )}
        {(m.references?.length ?? 0) > 0 && (
          <>
            <dt>References</dt>
            <dd>
              <code>{m.references?.join(' ')}</code>
            </dd>
          </>
        )}
        <dt>Folder</dt>
        <dd>{m.folder}</dd>
        <dt>Labels / flags</dt>
        <dd>{[...(m.labels ?? []), ...(m.flags ?? [])].join(', ') || '—'}</dd>
        <dt>Raw MIME</dt>
        <dd>
          {m.raw_blob_ref ? (
            rawMimeHref ? (
              <Link to={rawMimeHref(m.raw_blob_ref)} data-testid="raw-mime-link">
                {m.raw_blob_ref}
              </Link>
            ) : (
              <code>{m.raw_blob_ref}</code>
            )
          ) : (
            '—'
          )}
        </dd>
      </dl>
      <h5 id={headersId}>All headers</h5>
      {Object.keys(m.headers ?? {}).length === 0 ? (
        <p className="muted">No other headers recorded.</p>
      ) : (
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- scrollable region must be keyboard-reachable (axe scrollable-region-focusable)
        <pre className="json" tabIndex={0} role="region" aria-labelledby={headersId}>
          {JSON.stringify(m.headers, null, 2)}
        </pre>
      )}
    </div>
  );
}

/** Fetches one attachment's bytes (with the operator key) for a download. */
export type AttachmentLoader = (m: CanonicalMessage, a: AttachmentRef) => Promise<{ blob: Blob; filename: string | null }>;

function AttachmentItem({ m, a, load }: { m: CanonicalMessage; a: AttachmentRef; load?: AttachmentLoader }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState('');
  const name = a.filename || '(unnamed)';
  const download = async (loader: AttachmentLoader) => {
    setBusy(true);
    setError(null);
    setDone('');
    try {
      const { blob, filename } = await loader(m, a);
      // Opaque bytes under a sanitised name: never rendered or opened in the page.
      const saveAs = saveBlob(blob, [filename, a.filename], `attachment-${a.id}`);
      setDone(`Downloaded ${saveAs}`);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <li>
      <span className="mb-att-name">{name}</span>{' '}
      <span className="muted">
        {a.mime_type} · {bytes(a.size)}
        {a.inline ? ' · inline' : ''}
      </span>{' '}
      {load && (
        <button type="button" className="link" disabled={busy} aria-label={`Download ${name}`} onClick={() => void download(load)}>
          {busy ? 'Downloading…' : 'Download'}
        </button>
      )}
      <span role="status" className="muted" data-testid={`attachment-status-${a.id}`}>
        {done}
      </span>
      {error != null && <ErrorDisplay error={error} title={`Could not download ${name}`} />}
    </li>
  );
}

function Attachments({ m, load }: { m: CanonicalMessage; load?: AttachmentLoader }) {
  const list = m.attachments ?? [];
  if (list.length === 0) return null;
  return (
    <section aria-label="Attachments" className="mb-attachments">
      <h4>
        {list.length} attachment{list.length === 1 ? '' : 's'}
      </h4>
      <ul data-testid="attachments">
        {list.map((a) => (
          <AttachmentItem key={a.id} m={m} a={a} load={load} />
        ))}
      </ul>
      {load && <p className="hint">Downloads save the file as sent; the dashboard never opens attachments itself.</p>}
    </section>
  );
}

function ThreadEntry({ t, onOpen }: { t: CanonicalMessage; onOpen: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="mb-thread-item">
      <div className="row space-between">
        <span>
          <strong>{address(t.from)}</strong> · <VirtualTime value={t.received_at ?? t.created_at} />
        </span>
        <span className="row">
          <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? 'Collapse' : 'Expand'}
            <span className="visually-hidden"> message from {address(t.from)}</span>
          </button>
          <button type="button" className="link" onClick={() => onOpen(t.id)}>
            Open {subjectOf(t)}
          </button>
        </span>
      </div>
      {open ? <SafeEmailBody text={t.body_text} html={t.body_html} /> : <p className="muted mb-snippet">{snippet(t) || '(no text)'}</p>}
    </li>
  );
}

/** The selected message, its attachments and the rest of its thread (oldest first). */
export function ReadingPane({
  message,
  thread,
  focusOnLoad,
  onOpen,
  rawMimeHref,
  downloadAttachment,
}: {
  message: CanonicalMessage;
  thread: CanonicalMessage[];
  focusOnLoad: boolean;
  onOpen: (id: string) => void;
  rawMimeHref?: (ref: string) => string;
  downloadAttachment?: AttachmentLoader;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const [showHeaders, setShowHeaders] = useState(false);
  useEffect(() => {
    if (focusOnLoad) heading.current?.focus();
  }, [message.id, focusOnLoad]);
  const m = message;
  const others = thread.filter((t) => t.id !== m.id);
  return (
    <article aria-labelledby="msg-h" className="message mb-reading" data-testid="message-view">
      <h3 id="msg-h" tabIndex={-1} ref={heading}>
        {subjectOf(m)}
      </h3>
      <dl className="kv">
        <dt>From</dt>
        <dd>{address(m.from)}</dd>
        <dt>To</dt>
        <dd>{addresses(m.to) || '—'}</dd>
        {(m.cc?.length ?? 0) > 0 && (
          <>
            <dt>Cc</dt>
            <dd>{addresses(m.cc)}</dd>
          </>
        )}
        <dt>Date</dt>
        <dd>
          <VirtualTime value={m.received_at ?? m.created_at} /> <span className="muted">(virtual time)</span>
        </dd>
      </dl>
      <button type="button" aria-expanded={showHeaders} aria-controls="mb-headers" onClick={() => setShowHeaders(!showHeaders)}>
        {showHeaders ? 'Hide headers' : 'Show headers'}
      </button>
      <div id="mb-headers">{showHeaders && <Headers m={m} rawMimeHref={rawMimeHref} />}</div>
      <Attachments m={m} load={downloadAttachment} />
      <SafeEmailBody key={m.id} text={m.body_text} html={m.body_html} />
      <section aria-labelledby="thr-h" className="mb-thread">
        <h4 id="thr-h">Thread ({thread.length} message{thread.length === 1 ? '' : 's'})</h4>
        {others.length === 0 ? (
          <p className="muted">This message is the only one in its thread.</p>
        ) : (
          <ol data-testid="thread">
            {thread.map((t) =>
              t.id === m.id ? (
                <li key={t.id} aria-current="true" className="mb-thread-item mb-thread-current">
                  <strong>{address(t.from)}</strong> · <VirtualTime value={t.received_at ?? t.created_at} /> — this message
                </li>
              ) : (
                <ThreadEntry key={t.id} t={t} onOpen={onOpen} />
              ),
            )}
          </ol>
        )}
      </section>
    </article>
  );
}
