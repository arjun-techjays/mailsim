// Local no-auth mode: `serve --local-no-auth` prints a dashboard link such as
// http://127.0.0.1:5173/#local_token=<token>. The fragment never reaches a server; the token is
// read once, removed from the address bar (and history), and kept in this tab's session only.
const PATTERN = /(?:^#|&)local_token=([A-Za-z0-9_-]{16,256})(?:&|$)/;
let cached: string | null | undefined;

export function takeLaunchToken(): string | null {
  if (cached !== undefined) return cached; // React StrictMode runs initialisers twice
  const match = PATTERN.exec(window.location.hash);
  cached = match ? (match[1] as string) : null;
  if (match) window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search);
  return cached;
}

/** Tests only. */
export function resetLaunchToken(): void {
  cached = undefined;
}
