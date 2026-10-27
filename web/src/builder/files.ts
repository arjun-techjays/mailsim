// Browser-side file helpers: uploads are read as base64 and sent to the API as JSON. Nothing
// here parses or renders file contents.

export function bytesToBase64(bytes: Uint8Array): string {
  let bin = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(bin);
}

export function textToBase64(text: string): string {
  return bytesToBase64(new TextEncoder().encode(text));
}

export function fileBytes(file: Blob): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(new Error('Could not read the file.'));
    reader.readAsArrayBuffer(file);
  });
}

export async function fileToBase64(file: Blob): Promise<string> {
  return bytesToBase64(await fileBytes(file));
}

/** Decoded size of a base64 string, in bytes. */
export function base64Size(b64: string): number {
  const pad = b64.endsWith('==') ? 2 : b64.endsWith('=') ? 1 : 0;
  return Math.max(0, Math.floor((b64.length * 3) / 4) - pad);
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** Same limit as the API (5 MB per fixture/attachment). */
export const MAX_FILE_BYTES = 5 * 1024 * 1024;
/** At most this many attachments per composed message (API limit). */
export const MAX_ATTACHMENTS = 20;
/** Attachment bytes per composed message: base64 in the .eml adds a third, plus room for the
 * headers and body, and the .eml must stay within MAX_FILE_BYTES. */
export const ATTACHMENT_BUDGET_BYTES = Math.floor((MAX_FILE_BYTES * 3) / 4) - 64 * 1024;
/** All message files of one scenario: the publish request (base64 in JSON) must stay well under
 * the API's 40 MB request body limit. */
export const DRAFT_FIXTURE_BUDGET_BYTES = 28 * 1024 * 1024;

/** Decoded bytes of the given fixtures. */
export function fixturesBytes(fixtures: Record<string, string>, paths: readonly string[]): number {
  return [...new Set(paths)].reduce((n, p) => n + base64Size(fixtures[p] ?? ''), 0);
}

/** `Invoice #1 (final).eml` -> `invoice-1-final.eml`. */
export function safeFileName(name: string): string {
  const dot = name.lastIndexOf('.');
  const stem = dot > 0 ? name.slice(0, dot) : name;
  const ext = dot > 0 ? name.slice(dot + 1).toLowerCase().replace(/[^a-z0-9]/g, '') : '';
  const slug =
    stem
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'message';
  return ext ? `${slug}.${ext}` : slug;
}

const PLAIN_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,150}$/;

/**
 * `fixtures/<name>`, made unique against `taken` (`-2`, `-3`, ... before the extension). A plain
 * name (letters, digits, `.`, `_`, `-`) is kept EXACTLY (`003-deal-.eml` keeps its trailing
 * hyphen, so it still matches a scenario that lists it); anything else becomes a slug.
 */
export function uniqueFixturePath(name: string, taken: readonly string[]): string {
  const safe = PLAIN_NAME.test(name) ? name : safeFileName(name);
  const dot = safe.lastIndexOf('.');
  const stem = dot > 0 ? safe.slice(0, dot) : safe;
  const ext = dot > 0 ? safe.slice(dot) : '';
  let path = `fixtures/${safe}`;
  for (let n = 2; taken.includes(path); n += 1) path = `fixtures/${stem}-${n}${ext}`;
  return path;
}

/** FNV-1a over the whole text: detects any content change (not just length or tail). */
export function contentHash(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(36);
}
