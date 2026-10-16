/**
 * Hands the browser a Blob to save under `filename`: a short-lived object URL on an
 * `<a download>` that is revoked right after the click. Nothing is navigated to or rendered,
 * so the strict CSP stays intact and file content never reaches the page.
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.rel = 'noopener';
    a.click();
  } finally {
    URL.revokeObjectURL(url);
  }
}
