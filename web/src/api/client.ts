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
  timeoutMs?: number;
}

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

async function errorFromResponse(res: Response): Promise<ApiError> {
  const headerRid = res.headers.get('x-request-id');
  let parsed: EnvelopeShape | null = null;
  const text = await res.text();
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

export async function apiRequest<T>(
  cfg: ApiConfig,
  method: 'GET' | 'POST' | 'PATCH',
  path: string,
  opts: RequestOptions = {},
): Promise<T> {
  const timeoutMs = cfg.timeoutMs ?? defaultTimeoutMs();
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const onOuterAbort = () => controller.abort();
  opts.signal?.addEventListener('abort', onOuterAbort);

  const headers: Record<string, string> = { Accept: 'application/json' };
  if (cfg.apiKey) headers.Authorization = `Bearer ${cfg.apiKey}`;
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  if (opts.idempotent) headers['Idempotency-Key'] = newIdempotencyKey();

  let res: Response;
  try {
    res = await fetch(buildUrl(cfg.baseUrl, path, opts.query), {
      method,
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: controller.signal,
      credentials: 'omit',
      referrerPolicy: 'no-referrer',
    });
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

  if (!res.ok) throw await errorFromResponse(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
