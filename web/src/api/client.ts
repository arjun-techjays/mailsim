// Minimal typed fetch client for the mailsim /v1 REST API.
// - Bearer API key from the in-memory session (never put in URLs).
// - Parses the single error envelope {"error": {code, message, details, request_id}}.
// - Per-request timeout via AbortController, surfaced as a distinct error kind.

export type ApiErrorKind = 'http' | 'timeout' | 'network';

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly requestId: string | null;

  constructor(init: {
    kind: ApiErrorKind;
    message: string;
    status?: number | null;
    code: string;
    details?: Record<string, unknown>;
    requestId?: string | null;
  }) {
    super(init.message);
    this.name = 'ApiError';
    this.kind = init.kind;
    this.status = init.status ?? null;
    this.code = init.code;
    this.details = init.details ?? {};
    this.requestId = init.requestId ?? null;
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

export interface ApiConfig {
  baseUrl: string;
  apiKey: string;
  /** Local no-auth mode launch token (sent only when there is no API key). */
  localToken?: string;
  timeoutMs?: number;
}

export const LOCAL_TOKEN_HEADER = 'X-Mailsim-Local-Token';

export type QueryValue = string | number | boolean | null | undefined | readonly string[];

export interface RequestOptions {
  query?: Record<string, QueryValue>;
  body?: unknown;
  /** Send an Idempotency-Key header (create run, inject, clock, reset, ...). */
  idempotent?: boolean;
  signal?: AbortSignal;
}

export const DEFAULT_TIMEOUT_MS = 15_000;

export function defaultTimeoutMs(): number {
  const raw = Number(import.meta.env.VITE_REQUEST_TIMEOUT_MS);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_TIMEOUT_MS;
}

export function buildUrl(baseUrl: string, path: string, query?: Record<string, QueryValue>): string {
  const base = baseUrl.trim().replace(/\/+$/, '');
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === null || value === '') continue;
    if (Array.isArray(value)) {
      for (const v of value) params.append(key, v);
    } else {
      params.append(key, String(value));
    }
  }
  const qs = params.toString();
  return `${base}${path}${qs ? `?${qs}` : ''}`;
}

function newIdempotencyKey(): string {
  return `dash-${crypto.randomUUID()}`;
}

interface EnvelopeShape {
  error?: { code?: unknown; message?: unknown; details?: unknown; request_id?: unknown };
}

function errorFromResponse(res: Response, text: string): ApiError {
  const headerRid = res.headers.get('x-request-id');
  let parsed: EnvelopeShape | null = null;
  try {
    parsed = JSON.parse(text) as EnvelopeShape;
  } catch {
    parsed = null;
  }
  const err = parsed?.error;
  if (err && typeof err.code === 'string') {
    return new ApiError({
      kind: 'http',
      status: res.status,
      code: err.code,
      message: typeof err.message === 'string' ? err.message : res.statusText,
      details:
        err.details && typeof err.details === 'object'
          ? (err.details as Record<string, unknown>)
          : {},
      requestId: typeof err.request_id === 'string' ? err.request_id : headerRid,
    });
  }
  return new ApiError({
    kind: 'http',
    status: res.status,
    code: `HTTP_${res.status}`,
    message: text.slice(0, 300) || res.statusText || 'Request failed',
    requestId: headerRid,
  });
}

async function send(
  cfg: ApiConfig,
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  path: string,
  opts: RequestOptions,
  accept: string,
): Promise<{ res: Response; text: string }> {
  const timeoutMs = cfg.timeoutMs ?? defaultTimeoutMs();
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const onOuterAbort = () => controller.abort();
  opts.signal?.addEventListener('abort', onOuterAbort);

  const headers: Record<string, string> = { Accept: accept };
  if (cfg.apiKey) headers.Authorization = `Bearer ${cfg.apiKey}`;
  else if (cfg.localToken) headers[LOCAL_TOKEN_HEADER] = cfg.localToken;
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  if (opts.idempotent) headers['Idempotency-Key'] = newIdempotencyKey();

  let res: Response;
  let text: string;
  try {
    // The timer stays armed until the whole body has been read.
    res = await fetch(buildUrl(cfg.baseUrl, path, opts.query), {
      method,
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: controller.signal,
      credentials: 'omit',
      referrerPolicy: 'no-referrer',
    });
    text = await res.text();
  } catch (e) {
    if (timedOut) {
      throw new ApiError({
        kind: 'timeout',
        code: 'TIMEOUT',
        message: `The API did not respond within ${Math.round(timeoutMs / 100) / 10}s.`,
      });
    }
    if (opts.signal?.aborted) throw e;
    throw new ApiError({
      kind: 'network',
      code: 'NETWORK_ERROR',
      message: 'Could not reach the API. Check the base URL and that the server is running.',
    });
  } finally {
    clearTimeout(timer);
    opts.signal?.removeEventListener('abort', onOuterAbort);
  }

  if (!res.ok) throw errorFromResponse(res, text);
  return { res, text };
}

export async function apiRequest<T>(
  cfg: ApiConfig,
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  path: string,
  opts: RequestOptions = {},
): Promise<T> {
  const { res, text } = await send(cfg, method, path, opts, 'application/json');
  if (res.status === 204 || text === '') return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError({
      kind: 'http',
      status: res.status,
      code: 'INVALID_RESPONSE',
      message: 'The API returned a response that is not JSON.',
      requestId: res.headers.get('x-request-id'),
    });
  }
}

/** GET a non-JSON resource (e.g. report exports) as text, with the same auth/timeout/errors. */
export async function apiText(
  cfg: ApiConfig,
  path: string,
  query?: Record<string, QueryValue>,
): Promise<{ text: string; contentType: string }> {
  const { res, text } = await send(cfg, 'GET', path, { query }, '*/*');
  return { text, contentType: res.headers.get('content-type') ?? 'application/octet-stream' };
}
