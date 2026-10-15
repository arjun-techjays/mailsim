import { ApiError } from '../api/client';
import type { CanonicalMessage, FolderOut, MessagePage } from '../api/types';
import type { ParsedSnapshot } from './snapshot';

/** Read-only mailbox access used by the inspector. */
export interface MailboxSource {
  /** Stable cache-key prefix for react-query. */
  key: readonly unknown[];
  listFolders(): Promise<FolderOut[]>;
  listMessages(folder: string | null, cursor: string | null, pageSize: number): Promise<MessagePage>;
  getThread(threadId: string): Promise<CanonicalMessage[]>;
  getMessage(messageId: string): Promise<CanonicalMessage>;
}

function notFound(what: string): ApiError {
  return new ApiError({ kind: 'http', status: 404, code: 'NOT_FOUND', message: `${what} not found in run state` });
}

/**
 * Controller/evaluator view: the contract gives them no paginated mailbox read API, so data
 * comes from the run state snapshot and is paginated client-side (opaque offset cursors).
 */
export function snapshotSource(snap: ParsedSnapshot, mailboxId: string, version: number): MailboxSource {
  const inMailbox = snap.messages.filter((m) => m.mailbox_id === mailboxId);
  return {
    key: ['state-mailbox', mailboxId, version],
    listFolders: async () =>
      snap.folders.filter((f) => f.mailbox_id === mailboxId).map(({ id, name, system_type }) => ({ id, name, system_type })),
    listMessages: async (folder, cursor, pageSize) => {
      const all = inMailbox.filter((m) => !folder || m.folder === folder);
      const offset = cursor ? Number(cursor) : 0;
      const end = offset + pageSize;
      return { items: all.slice(offset, end), next_cursor: end < all.length ? String(end) : null };
    },
    getThread: async (threadId) => inMailbox.filter((m) => m.thread_id === threadId),
    getMessage: async (messageId) => {
      const m = inMailbox.find((x) => x.id === messageId);
      if (!m) throw notFound(`message ${messageId}`);
      return m;
    },
  };
}
