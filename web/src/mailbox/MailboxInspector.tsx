import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import type { CanonicalMessage } from '../api/types';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { address, addresses, bytes } from '../components/format';
import { JsonBlock } from '../components/JsonBlock';
import { SafeEmailBody } from '../components/SafeEmailBody';
import { Empty, Loading } from '../components/Status';
import { useCursorList } from '../hooks/useCursorList';
import type { MailboxSource } from './source';

export const MESSAGE_PAGE_SIZE = 20;

function MessageView({
  source,
  messageId,
  onSelect,
  rawMimeHref,
}: {
  source: MailboxSource;
  messageId: string;
  onSelect: (id: string) => void;
  rawMimeHref?: (ref: string) => string;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const message = useQuery({
    queryKey: [...source.key, 'message', messageId],
    queryFn: () => source.getMessage(messageId),
  });
  const threadId = message.data?.thread_id;
  const thread = useQuery({
    queryKey: [...source.key, 'thread', threadId],
    queryFn: () => source.getThread(threadId as string),
    enabled: !!threadId,
  });
  useEffect(() => {
    if (message.data) heading.current?.focus();
  }, [message.data]);

  if (message.isPending) return <Loading label="Loading message…" />;
  if (message.isError) return <ErrorDisplay error={message.error} title="Could not load message" />;
  const m = message.data;
  return (
    <article aria-labelledby="msg-h" className="message" data-testid="message-view">
      <h3 id="msg-h" tabIndex={-1} ref={heading}>
        {m.subject || '(no subject)'}
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
        <dd>{m.received_at ?? m.created_at}</dd>
        <dt>Folder</dt>
        <dd>{m.folder}</dd>
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
      <JsonBlock value={m.headers} label="All headers" />
      <section aria-labelledby="att-h">
        <h4 id="att-h">Attachments</h4>
        {(m.attachments?.length ?? 0) === 0 ? (
          <p className="muted">No attachments.</p>
        ) : (
          <table data-testid="attachments">
            <thead>
              <tr>
                <th scope="col">File name</th>
                <th scope="col">Type</th>
                <th scope="col">Size</th>
                <th scope="col">Checksum</th>
              </tr>
            </thead>
            <tbody>
              {m.attachments?.map((a) => (
                <tr key={a.id}>
                  <th scope="row">{a.filename}</th>
                  <td>{a.mime_type}</td>
                  <td>{bytes(a.size)}</td>
                  <td>
                    <code>{a.checksum}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <SafeEmailBody key={m.id} text={m.body_text} html={m.body_html} />
      <section aria-labelledby="thr-h">
        <h4 id="thr-h">Thread</h4>
        {thread.isPending ? (
          <Loading label="Loading thread…" />
        ) : thread.isError ? (
          <ErrorDisplay error={thread.error} title="Could not load thread" />
        ) : (
          <ol data-testid="thread">
            {thread.data.map((t) => (
              <li key={t.id}>
                {t.id === m.id ? (
                  <span aria-current="true">
                    <strong>{t.subject || '(no subject)'}</strong> — {address(t.from)} (this message)
                  </span>
                ) : (
                  <button type="button" className="link" onClick={() => onSelect(t.id)}>
                    {t.subject || '(no subject)'} — {address(t.from)}
                  </button>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>
    </article>
  );
}

function MessageList({
  source,
  folder,
  selected,
  onSelect,
}: {
  source: MailboxSource;
  folder: string | null;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const list = useCursorList<CanonicalMessage>([...source.key, 'messages', folder], (cursor) =>
    source.listMessages(folder, cursor, MESSAGE_PAGE_SIZE),
  );
  if (list.isLoading) return <Loading label="Loading messages…" />;
  if (list.error && list.items.length === 0) return <ErrorDisplay error={list.error} title="Could not load messages" />;
  if (list.items.length === 0) return <Empty>No messages in this folder.</Empty>;
  return (
    <>
      <table data-testid="message-list">
        <caption>
          {list.items.length} message(s) loaded{list.hasMore ? ', more available' : ''}
        </caption>
        <thead>
          <tr>
            <th scope="col">Subject</th>
            <th scope="col">From</th>
            <th scope="col">Folder</th>
            <th scope="col">Date</th>
          </tr>
        </thead>
        <tbody>
          {list.items.map((m) => (
            <tr key={m.id} className={m.id === selected ? 'selected' : undefined}>
              <th scope="row">
                <button
                  type="button"
                  className="link"
                  aria-current={m.id === selected ? 'true' : undefined}
                  onClick={() => onSelect(m.id)}
                >
                  {m.subject || '(no subject)'}
                </button>
              </th>
              <td>{address(m.from)}</td>
              <td>{m.folder}</td>
              <td>{m.received_at ?? m.created_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {list.error && <ErrorDisplay error={list.error} title="Could not load more messages" />}
      {list.hasMore && (
        <button type="button" onClick={list.loadMore} disabled={list.isFetchingMore}>
          {list.isFetchingMore ? 'Loading…' : 'Load more messages'}
        </button>
      )}
    </>
  );
}

export function MailboxInspector({
  source,
  selectedMessage,
  onSelectMessage,
  rawMimeHref,
}: {
  source: MailboxSource;
  selectedMessage: string | null;
  onSelectMessage: (id: string) => void;
  rawMimeHref?: (ref: string) => string;
}) {
  const [folder, setFolder] = useState<string | null>(null);
  const folders = useQuery({ queryKey: [...source.key, 'folders'], queryFn: () => source.listFolders() });

  return (
    <div className="inspector">
      <nav aria-label="Folders" className="folders">
        <h3>Folders</h3>
        {folders.isPending ? (
          <Loading />
        ) : folders.isError ? (
          <ErrorDisplay error={folders.error} title="Could not load folders" />
        ) : (
          <ul>
            {[{ id: '', name: null as string | null, label: 'All folders' }, ...folders.data.map((f) => ({ id: f.id, name: f.name, label: f.name }))].map(
              (f) => (
                <li key={f.id || 'all'}>
                  <button type="button" aria-pressed={folder === f.name} onClick={() => setFolder(f.name)}>
                    {f.label}
                  </button>
                </li>
              ),
            )}
          </ul>
        )}
      </nav>
      <section aria-label="Messages" className="messages">
        <MessageList source={source} folder={folder} selected={selectedMessage} onSelect={onSelectMessage} />
      </section>
      <section aria-label="Message" className="message-pane">
        {selectedMessage ? (
          <MessageView source={source} messageId={selectedMessage} onSelect={onSelectMessage} rawMimeHref={rawMimeHref} />
        ) : (
          <p className="muted">Select a message to see its headers, body, attachments and thread.</p>
        )}
      </section>
    </div>
  );
}
