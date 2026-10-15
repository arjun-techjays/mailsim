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

export function fileToBase64(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(bytesToBase64(new Uint8Array(reader.result as ArrayBuffer)));
    reader.onerror = () => reject(new Error('Could not read the file.'));
    reader.readAsArrayBuffer(file);
  });
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

/** `fixtures/<name>`, made unique against `taken` (`-2`, `-3`, ... before the extension). */
export function uniqueFixturePath(name: string, taken: readonly string[]): string {
  const safe = safeFileName(name);
  const dot = safe.lastIndexOf('.');
  const stem = dot > 0 ? safe.slice(0, dot) : safe;
  const ext = dot > 0 ? safe.slice(dot) : '';
  let path = `fixtures/${safe}`;
  let n = 2;
  while (taken.includes(path)) {
    path = `fixtures/${stem}-${n}${ext}`;
    n += 1;
  }
  return path;
}
