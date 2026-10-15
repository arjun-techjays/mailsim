// Mail-client view of every mailbox in a run: mailboxes and folders (left), messages of the
// selected folder (middle, newest first, paged by the operator inspect API) and the reading pane
// with the whole thread (right). Email HTML is only rendered by SafeEmailBody (empty sandbox).
import { useMemo, useState } from 'react';
import type { CanonicalMessage, RunOut } from '../api/types';
import { hasRole, useApi, useRole } from '../auth/session';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { address } from '../components/format';
import { Empty, Loading } from '../components/Status';
import { useCursorList } from '../hooks/useCursorList';
import { runKeys, runLinks } from '../run/keys';
import { ComposeInject } from './ComposeInject';
import { ReadingPane } from './ReadingPane';
import type { ParsedSnapshot } from './snapshot';
import { isUnread, mailboxTitle, useRunMailboxes, type BrowserMailbox } from './useRunMailboxes';
import { VirtualTime } from './VirtualTime';
import './browser.css';

export const MESSAGE_PAGE_SIZE = 20;

export interface BrowserSelection {
  mailbox?: string | null;
  folder?: string | null;
  message?: string | null;
}

function MailboxNav({
  mailboxes,
  mailboxId,
  folderId,
  onSelect,
}: {
  mailboxes: BrowserMailbox[];
  mailboxId: string;
  folderId: string | null;
  onSelect: (mailbox: string, folder: string | null) => void;
}) {
  return (
    <nav aria-label="Mailboxes and folders" className="mb-nav">
      <h3>Mailboxes ({mailboxes.length})</h3>
      <ul className="mb-mailboxes">
        {mailboxes.map((mb) => {
          const current = mb.id === mailboxId;
          return (
            <li key={mb.id} className={current ? 'mb-mailbox mb-current' : 'mb-mailbox'} data-testid={`mailbox-${mb.id}`}>
              <button
                type="button"
                className="mb-mailbox-button"
                aria-pressed={current && folderId === null}
                onClick={() => onSelect(mb.id, null)}
              >
                <span className="mb-mailbox-name">{mailboxTitle(mb)}</span>
                {mb.isAgent && <span className="badge mb-agent-badge">Agent under test</span>}
                <span className="mb-mailbox-email">{mb.email}</span>
                <span className="mb-counts">
                  {mb.total} message{mb.total === 1 ? '' : 's'}
                  {mb.unread > 0 ? `, ${mb.unread} unread` : ''}
                </span>
              </button>
              <ul className="mb-folders" aria-label={`Folders of ${mailboxTitle(mb)}`}>
                {mb.folders.map((f) => (
                  <li key={f.id}>
                    <button
                      type="button"
                      aria-pressed={current && folderId === f.id}
                      onClick={() => onSelect(mb.id, f.id)}
                      aria-label={`${f.name}, ${f.total} message${f.total === 1 ? '' : 's'}${f.unread > 0 ? `, ${f.unread} unread` : ''} (${mailboxTitle(mb)})`}
                    >
                      <span>{f.name}</span>
                      <span className="mb-count" aria-hidden="true">
                        {f.total}
                        {f.unread > 0 && <strong> · {f.unread} new</strong>}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

function MessageList({
  runId,
  mailbox,
  folderId,
  version,
  selected,
  onSelect,
}: {
  runId: string;
  mailbox: BrowserMailbox;
  folderId: string | null;
  version: number;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const api = useApi();
  const list = useCursorList<CanonicalMessage>([...runKeys.inspect(runId), mailbox.id, folderId, version], (cursor) =>
    api.inspectMessages(runId, mailbox.id, { folder: folderId, cursor, pageSize: MESSAGE_PAGE_SIZE, order: 'desc' }),
  );
  if (list.isLoading) return <Loading label="Loading messages…" />;
  if (list.error && list.items.length === 0) return <ErrorDisplay error={list.error} title="Could not load messages" />;
  if (list.items.length === 0) return <Empty>No messages in this folder.</Empty>;
  return (
    <>
      <table data-testid="message-list" className="mb-list">
        <caption>
          {list.items.length} message(s) loaded{list.hasMore ? ', more available' : ''}, newest first
        </caption>
        <thead>
          <tr>
            <th scope="col">
              <span className="visually-hidden">Status</span>
            </th>
            <th scope="col">From</th>
            <th scope="col">Subject</th>
            <th scope="col">Date</th>
          </tr>
        </thead>
        <tbody>
          {list.items.map((m) => {
            const unread = isUnread(m);
            const flagged = (m.flags ?? []).includes('FLAGGED');
            const attachments = (m.attachments ?? []).length;
            return (
              <tr key={m.id} className={[m.id === selected ? 'selected' : '', unread ? 'mb-unread' : ''].join(' ').trim() || undefined}>
                <td className="mb-status">
                  {unread && (
                    <span className="mb-dot" title="Unread">
                      <span className="visually-hidden">Unread</span>
                    </span>
                  )}
                  {flagged && (
                    <span className="mb-icon" title="Flagged">
                      <span aria-hidden="true">⚑</span>
                      <span className="visually-hidden">Flagged</span>
                    </span>
                  )}
                  {attachments > 0 && (
                    <span className="mb-icon" title={`${attachments} attachment(s)`}>
                      <span aria-hidden="true">📎</span>
                      <span className="visually-hidden">Has attachments</span>
                    </span>
                  )}
                </td>
                <td title={address(m.from)}>{m.from.name || m.from.email}</td>
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
                <td className="mb-date">
                  <VirtualTime value={m.received_at ?? m.created_at} short />
                </td>
              </tr>
            );
          })}
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

export function MailboxBrowser({
  run,
  snapshot,
  version,
  error,
  selection,
  onChange,
}: {
  run: RunOut;
  snapshot: ParsedSnapshot | undefined;
  /** Changes whenever the run state was refetched (refreshes the message list). */
  version: number;
  error: unknown;
  selection: BrowserSelection;
  onChange: (next: BrowserSelection) => void;
}) {
  const controller = hasRole(useRole(), ['controller']);
  const { mailboxes, error: listError } = useRunMailboxes(run, snapshot);
  const [userSelected, setUserSelected] = useState(false);
  const [composing, setComposing] = useState(false);

  const messageParam = selection.message ?? null;
  const mailbox = useMemo(() => {
    if (!mailboxes || mailboxes.length === 0) return null;
    const byParam = mailboxes.find((m) => m.id === selection.mailbox);
    if (byParam) return byParam;
    const owner = messageParam ? snapshot?.messages.find((m) => m.id === messageParam)?.mailbox_id : undefined;
    return mailboxes.find((m) => m.id === owner) ?? (mailboxes[0] as BrowserMailbox);
  }, [mailboxes, selection.mailbox, messageParam, snapshot]);
  const folderId = mailbox && mailbox.folders.some((f) => f.id === selection.folder) ? (selection.folder as string) : null;

  if (error) return <ErrorDisplay error={error} title="Could not load run state" />;
  if (listError) return <ErrorDisplay error={listError} title="Could not load the run’s mailboxes" />;
  if (!snapshot || !mailboxes) return <Loading label="Loading mailboxes…" />;
  if (!mailbox) return <Empty>This run has no mailboxes.</Empty>;

  const folder = mailbox.folders.find((f) => f.id === folderId);
  const selected = messageParam ? snapshot.messages.find((m) => m.id === messageParam) : undefined;
  const thread = selected
    ? snapshot.messages
        .filter((m) => m.mailbox_id === selected.mailbox_id && m.thread_id === selected.thread_id)
        .sort((a, b) => (a.received_at ?? a.created_at).localeCompare(b.received_at ?? b.created_at))
    : [];
  const select = (id: string) => {
    setUserSelected(true);
    setComposing(false);
    onChange({ mailbox: mailbox.id, folder: folderId, message: id });
  };

  return (
    <div className="mb-browser" data-testid="mailbox-browser">
      <MailboxNav
        mailboxes={mailboxes}
        mailboxId={mailbox.id}
        folderId={folderId}
        onSelect={(mb, f) => {
          setComposing(false);
          onChange({ mailbox: mb, folder: f, message: null });
        }}
      />
      <section aria-labelledby="mb-list-h" className="mb-messages">
        <div className="row space-between">
          <h3 id="mb-list-h">
            {mailboxTitle(mailbox)} · {folder ? folder.name : 'All folders'}
          </h3>
          {controller && (
            <button type="button" onClick={() => setComposing(true)} aria-expanded={composing}>
              Inject email here
            </button>
          )}
        </div>
        <p className="hint mb-who">
          {mailbox.isAgent ? 'This is the mailbox the agent under test works in.' : `A persona mailbox (${mailbox.email}).`}{' '}
          Reading here is not an agent action: nothing is recorded and no triggers fire.
        </p>
        <MessageList
          key={`${mailbox.id}:${folderId ?? ''}`}
          runId={run.id}
          mailbox={mailbox}
          folderId={folderId}
          version={version}
          selected={messageParam}
          onSelect={select}
        />
      </section>
      <section aria-label="Reading pane" className="mb-pane">
        {composing ? (
          <ComposeInject
            runId={run.id}
            mailbox={mailbox}
            mailboxes={mailboxes}
            replyCandidates={snapshot.messages
              .filter((m) => m.mailbox_id === mailbox.id)
              .sort((a, b) => (b.received_at ?? b.created_at).localeCompare(a.received_at ?? a.created_at))}
            onClose={() => setComposing(false)}
            onOpenMessage={select}
          />
        ) : selected ? (
          <ReadingPane
            message={selected}
            thread={thread}
            focusOnLoad={userSelected}
            onOpen={select}
            rawMimeHref={(ref) => runLinks.artifact(run.id, ref)}
          />
        ) : messageParam ? (
          <ErrorDisplay error={new Error(`Message ${messageParam} is not in this run (it may have been reset).`)} title="Could not load message" />
        ) : (
          <p className="muted mb-placeholder">Select a message to read it with its thread, attachments and headers.</p>
        )}
      </section>
    </div>
  );
}
