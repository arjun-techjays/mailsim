import { useMemo } from 'react';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { Empty, Loading } from '../components/Status';
import { MailboxInspector } from '../mailbox/MailboxInspector';
import { snapshotSource } from '../mailbox/source';
import type { ParsedSnapshot } from '../mailbox/snapshot';
import { runLinks } from './keys';

export function RunMailboxes({
  runId,
  snapshot,
  version,
  error,
  mailboxParam,
  messageParam,
  onChange,
}: {
  runId: string;
  snapshot: ParsedSnapshot | undefined;
  version: number;
  error: unknown;
  mailboxParam: string | null;
  messageParam: string | null;
  onChange: (next: { mailbox?: string; message?: string | null }) => void;
}) {
  const mailboxId = useMemo(() => {
    if (!snapshot) return null;
    if (mailboxParam && snapshot.mailboxes.some((m) => m.id === mailboxParam)) return mailboxParam;
    const owner = messageParam ? snapshot.messages.find((m) => m.id === messageParam)?.mailbox_id : undefined;
    return owner ?? snapshot.mailboxes[0]?.id ?? null;
  }, [snapshot, mailboxParam, messageParam]);

  const source = useMemo(
    () => (snapshot && mailboxId ? snapshotSource(snapshot, mailboxId, version) : null),
    [snapshot, mailboxId, version],
  );

  if (error) return <ErrorDisplay error={error} title="Could not load run state" />;
  if (!snapshot) return <Loading label="Loading mailbox state…" />;
  if (!source || !mailboxId) return <Empty>This run has no mailboxes.</Empty>;

  return (
    <section aria-labelledby="mbx-h">
      <h2 id="mbx-h">Mailboxes</h2>
      <p className="hint">Read from the run state snapshot (controller/evaluator view).</p>
      <div className="field inline">
        <label htmlFor="mbx-select">Mailbox</label>
        <select id="mbx-select" value={mailboxId} onChange={(e) => onChange({ mailbox: e.target.value, message: null })}>
          {snapshot.mailboxes.map((m) => (
            <option key={m.id} value={m.id}>
              {m.email} ({m.id})
            </option>
          ))}
        </select>
      </div>
      <MailboxInspector
        key={mailboxId}
        source={source}
        selectedMessage={messageParam}
        onSelectMessage={(id) => onChange({ mailbox: mailboxId, message: id })}
        rawMimeHref={(ref) => runLinks.artifact(runId, ref)}
      />
    </section>
  );
}
