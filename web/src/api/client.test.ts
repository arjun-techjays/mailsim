import { delay, http, HttpResponse } from 'msw';
import { server } from '../test/server';
import { ApiError, apiRequest, buildUrl, DEFAULT_TIMEOUT_MS, defaultTimeoutMs, isApiError } from './client';

const cfg = { baseUrl: 'http://api.test/', apiKey: 'k1' };

describe('buildUrl', () => {
  it('joins base and path, skips empty values and repeats arrays', () => {
    expect(buildUrl('http://x/ ', '/v1/a', { a: 1, b: '', c: null, d: undefined, e: ['x', 'y'], f: true })).toBe(
      'http://x/v1/a?a=1&e=x&e=y&f=true',
    );
    expect(buildUrl('', '/v1/a')).toBe('/v1/a');
  });
});

describe('defaultTimeoutMs', () => {
  afterEach(() => vi.unstubAllEnvs());
  it('uses the env override when valid', () => {
    vi.stubEnv('VITE_REQUEST_TIMEOUT_MS', '1234');
    expect(defaultTimeoutMs()).toBe(1234);
  });
  it('falls back to the default', () => {
    vi.stubEnv('VITE_REQUEST_TIMEOUT_MS', 'nope');
    expect(defaultTimeoutMs()).toBe(DEFAULT_TIMEOUT_MS);
  });
});

describe('apiRequest', () => {
  it('sends the bearer key, JSON body and an idempotency key', async () => {
    let seen: Request | null = null;
    server.use(
      http.post('http://api.test/v1/echo', async ({ request }) => {
        seen = request.clone();
        return HttpResponse.json({ ok: true });
      }),
    );
    const out = await apiRequest<{ ok: boolean }>(cfg, 'POST', '/v1/echo', { body: { a: 1 }, idempotent: true });
    expect(out).toEqual({ ok: true });
    const req = seen as unknown as Request;
    expect(req.headers.get('authorization')).toBe('Bearer k1');
    expect(req.headers.get('content-type')).toBe('application/json');
    expect(req.headers.get('idempotency-key')).toMatch(/^dash-/);
    expect(await req.json()).toEqual({ a: 1 });
    expect(req.url).not.toContain('k1');
  });

  it('omits auth when no key and handles 204', async () => {
    let auth: string | null = 'unset';
    server.use(
      http.get('http://api.test/v1/empty', ({ request }) => {
        auth = request.headers.get('authorization');
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await expect(apiRequest({ baseUrl: 'http://api.test', apiKey: '' }, 'GET', '/v1/empty')).resolves.toBeUndefined();
    expect(auth).toBeNull();
  });

  it('parses the error envelope including request_id and details', async () => {
    server.use(
      http.get('http://api.test/v1/fail', () =>
        HttpResponse.json(
          { error: { code: 'CONFLICT', message: 'nope', details: { a: 1 }, request_id: 'rid-1' } },
          { status: 409, headers: { 'x-request-id': 'hdr' } },
        ),
      ),
    );
    const err = (await apiRequest(cfg, 'GET', '/v1/fail').catch((e: unknown) => e)) as ApiError;
    expect(isApiError(err)).toBe(true);
    expect(err).toMatchObject({ kind: 'http', status: 409, code: 'CONFLICT', message: 'nope', requestId: 'rid-1' });
    expect(err.details).toEqual({ a: 1 });
  });

  it('tolerates partial envelopes and falls back to the x-request-id header', async () => {
    server.use(
      http.get('http://api.test/v1/partial', () =>
        HttpResponse.json({ error: { code: 'X', details: 'bad' } }, { status: 400, statusText: 'Bad', headers: { 'x-request-id': 'hdr-2' } }),
      ),
    );
    const err = (await apiRequest(cfg, 'GET', '/v1/partial').catch((e: unknown) => e)) as ApiError;
    expect(err).toMatchObject({ code: 'X', message: 'Bad', requestId: 'hdr-2', details: {} });
  });

  it('wraps non-JSON errors', async () => {
    server.use(http.get('http://api.test/v1/html', () => new HttpResponse('<h1>Bad gateway</h1>', { status: 502 })));
    const err = (await apiRequest(cfg, 'GET', '/v1/html').catch((e: unknown) => e)) as ApiError;
    expect(err).toMatchObject({ kind: 'http', status: 502, code: 'HTTP_502', message: '<h1>Bad gateway</h1>', requestId: null });
  });

  it('uses statusText or a default for empty error bodies', async () => {
    server.use(
      http.get('http://api.test/v1/blank', () => new HttpResponse(null, { status: 500 })),
      http.get('http://api.test/v1/blank2', () => new HttpResponse(null, { status: 599 })),
    );
    const err = (await apiRequest(cfg, 'GET', '/v1/blank').catch((e: unknown) => e)) as ApiError;
    expect(err.message).toBe('Internal Server Error');
    const err2 = (await apiRequest(cfg, 'GET', '/v1/blank2').catch((e: unknown) => e)) as ApiError;
    expect(err2.message).toBe('Request failed');
  });

  it('reports timeouts distinctly', async () => {
    server.use(
      http.get('http://api.test/v1/slow', async () => {
        await delay('infinite');
        return HttpResponse.json({});
      }),
    );
    const err = (await apiRequest({ ...cfg, timeoutMs: 30 }, 'GET', '/v1/slow').catch((e: unknown) => e)) as ApiError;
    expect(err).toMatchObject({ kind: 'timeout', code: 'TIMEOUT' });
    expect(err.message).toContain('0s');
  });

  it('reports network failures', async () => {
    server.use(http.get('http://api.test/v1/down', () => HttpResponse.error()));
    const err = (await apiRequest(cfg, 'GET', '/v1/down').catch((e: unknown) => e)) as ApiError;
    expect(err).toMatchObject({ kind: 'network', code: 'NETWORK_ERROR' });
  });

  it('re-throws caller aborts unchanged', async () => {
    server.use(
      http.get('http://api.test/v1/slow2', async () => {
        await delay('infinite');
        return HttpResponse.json({});
      }),
    );
    const ac = new AbortController();
    const p = apiRequest(cfg, 'GET', '/v1/slow2', { signal: ac.signal });
    ac.abort();
    const err = await p.catch((e: unknown) => e);
    expect(isApiError(err)).toBe(false);
  });
});
