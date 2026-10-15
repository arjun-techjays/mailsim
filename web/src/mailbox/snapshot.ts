// GET /v1/runs/{id}/state is typed as an open object in the contract. Its shape is the engine
// snapshot (engine/snapshot.py): {run, mailboxes[], folders[], messages[], attachments[], ...}
// with DB column names. Parse defensively and convert messages to CanonicalMessage so the
// mailbox inspector can use the same UI for controller/evaluator (state) and agent (mailbox API).
import type { Address, AttachmentRef, CanonicalMessage, FolderOut } from '../api/types';

export interface SnapshotMailbox {
  id: string;
  email: string;
}

export interface ParsedSnapshot {
  mailboxes: SnapshotMailbox[];
  folders: (FolderOut & { mailbox_id: string })[];
  messages: CanonicalMessage[];
}

type Row = Record<string, unknown>;

const rows = (v: unknown): Row[] => (Array.isArray(v) ? v.filter((x): x is Row => !!x && typeof x === 'object') : []);
const str = (v: unknown, d = ''): string => (typeof v === 'string' ? v : d);
const optStr = (v: unknown): string | null => (typeof v === 'string' ? v : null);
const strList = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);

function addr(v: unknown): Address {
  if (v && typeof v === 'object') {
    const o = v as Row;
    return { email: str(o.email), name: optStr(o.name) };
  }
  return { email: str(v) };
}
const addrList = (v: unknown): Address[] => (Array.isArray(v) ? v.map(addr) : []);

export function parseSnapshot(raw: Record<string, unknown>): ParsedSnapshot {
  const mailboxes = rows(raw.mailboxes).map((m) => ({ id: str(m.id), email: str(m.email) }));
  const folders = rows(raw.folders).map((f) => ({
    id: str(f.id),
    mailbox_id: str(f.mailbox_id),
    name: str(f.name),
    system_type: optStr(f.system_type),
  }));
  const folderName = new Map(folders.map((f) => [f.id, f.name]));
  const attachments = new Map<string, AttachmentRef[]>();
  for (const a of rows(raw.attachments)) {
    const list = attachments.get(str(a.message_id)) ?? [];
    list.push({
      id: str(a.id),
      filename: str(a.filename),
      mime_type: str(a.mime_type),
      size: typeof a.size === 'number' ? a.size : 0,
      checksum: str(a.checksum),
      blob_ref: str(a.blob_ref),
      inline: a.inline === true,
      content_id: optStr(a.content_id),
    });
    attachments.set(str(a.message_id), list);
  }
  const messages: CanonicalMessage[] = rows(raw.messages).map((m) => {
    const folderId = str(m.folder_id);
    return {
      id: str(m.id),
      mailbox_id: str(m.mailbox_id),
      message_id_header: str(m.message_id_header),
      thread_id: str(m.thread_id),
      folder: folderName.get(folderId) ?? folderId,
      from: addr(m.from),
      to: addrList(m.to),
      cc: addrList(m.cc),
      bcc: addrList(m.bcc),
      subject: str(m.subject),
      body_text: optStr(m.body_text),
      body_html: optStr(m.body_html),
      in_reply_to: optStr(m.in_reply_to),
      references: strList(m.references),
      created_at: str(m.created_at),
      received_at: optStr(m.received_at),
      labels: strList(m.labels),
      flags: strList(m.flags),
      headers: m.headers && typeof m.headers === 'object' ? (m.headers as Row) : {},
      is_draft: m.is_draft === true,
      raw_blob_ref: optStr(m.raw_blob_ref),
      attachments: attachments.get(str(m.id)) ?? [],
    };
  });
  return { mailboxes, folders, messages };
}
