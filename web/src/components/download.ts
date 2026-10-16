import { pickFilename } from '../api/filename';

/** How long the object URL lives after the click (long enough for the browser to start the save). */
export const REVOKE_AFTER_MS = 1000;

/**
 * Hands the browser bytes to save: a short-lived object URL on an `<a download>`, revoked
 * shortly after the click. The Blob is always `application/octet-stream` (never the server's
 * type), and the name is the first of `names` that is a safe base name (else `fallback`), so
 * nothing is rendered on the dashboard origin and no name can pick a directory or hide its
 * extension. Returns the name used.
 */
export function saveBlob(data: Blob | string, names: readonly (string | null | undefined)[], fallback = 'download'): string {
  const name = pickFilename(names, fallback);
  const url = URL.createObjectURL(new Blob([data], { type: 'application/octet-stream' }));
  try {
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    a.rel = 'noopener';
    a.click();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), REVOKE_AFTER_MS);
  }
  return name;
}
