const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);

/** True for http(s) URLs on a loopback host. */
export function isLoopback(origin: string): boolean {
  try {
    return LOCAL_HOSTS.has(new URL(origin).hostname);
  } catch {
    return false;
  }
}

/** API origin the dashboard talks to by default: the build-time override, else its own origin. */
export function defaultBaseUrl(): string {
  return import.meta.env.VITE_DEFAULT_API_BASE || window.location.origin;
}

export type BaseUrlResult = { ok: true; origin: string } | { ok: false; error: string };

/**
 * The API key is sent to whatever origin is entered here, so only accept a bare origin:
 * http(s) only, no credentials/path/query/fragment, and https unless the host is loopback.
 */
export function validateBaseUrl(input: string): BaseUrlResult {
  let url: URL;
  try {
    url = new URL(input.trim());
  } catch {
    return { ok: false, error: 'Enter a full URL such as https://mailsim.example.com.' };
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    return { ok: false, error: 'Only http and https URLs are allowed.' };
  }
  if (url.username || url.password) {
    return { ok: false, error: 'The URL must not contain credentials.' };
  }
  if (url.pathname !== '/' || url.search || url.hash) {
    return { ok: false, error: `Enter only the origin (${url.origin}), without a path, query or fragment.` };
  }
  if (url.protocol === 'http:' && !LOCAL_HOSTS.has(url.hostname)) {
    return { ok: false, error: 'Use https unless the API runs on localhost.' };
  }
  return { ok: true, origin: url.origin };
}
