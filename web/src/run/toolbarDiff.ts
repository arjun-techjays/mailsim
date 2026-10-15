import type { ParsedSnapshot } from '../mailbox/snapshot';

export interface Delivered {
  id: string;
  subject: string;
  /** Address of the mailbox the message is in. */
  to: string;
}

/** message id -> what the toolbar reports when it newly appears. */
export function deliveredIndex(snap: ParsedSnapshot): Map<string, Delivered> {
  const email = new Map(snap.mailboxes.map((m) => [m.id, m.email]));
  return new Map(snap.messages.map((m) => [m.id, { id: m.id, subject: m.subject, to: email.get(m.mailbox_id) ?? m.mailbox_id }]));
}
