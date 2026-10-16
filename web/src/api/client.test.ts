import { delay, http, HttpResponse } from 'msw';
import { server } from '../test/server';
import {
  ApiError,
  apiBlob,
  apiRequest,
  apiText,
  buildUrl,
  DEFAULT_TIMEOUT_MS,
  defaultTimeoutMs,
  dispositionFilename,
  isApiError,
  isRedacted,
  LONG_TIMEOUT_MS,
  longTimeoutMs,
} from './client';

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

describe('longTimeoutMs', () => {
  afterEach(() => vi.unstubAllEnvs());
  it('defaults to 120 s and honours the env override', () => {
    vi.stubEnv('VITE_LONG_REQUEST_TIMEOUT_MS', '');
    expect(longTimeoutMs()).toBe(LONG_TIMEOUT_MS);
    expect(LONG_TIMEOUT_MS).toBe(120_000);
    vi.stubEnv('VITE_LONG_REQUEST_TIMEOUT_MS', '900');
    expect(longTimeoutMs()).toBe(900);
  });
});

describe('dispositionFilename', () => {
  it('prefers filename*, falls back to filename, and keeps only a safe base name', () => {
    expect(dispositionFilename(null)).toBeNull();
    expect(dispositionFilename('attachment')).toBeNull();
    expect(dispositionFilename(`attachment; filename="a.pdf"; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf`)).toBe('résumé.pdf');
    expect(dispositionFilename(`attachment; filename*=UTF-8''"q.txt"`)).toBe('q.txt');
    expect(dispositionFilename('attachment; filename="say \\"hi\\".md"')).toBe('say "hi".md');
    expect(dispositionFilename('attachment; filename=plain.xml; size=3')).toBe('plain.xml');
    expect(dispositionFilename(`attachment; filename*=UTF-8''%E0%A4%A; filename="fallback.json"`)).toBe('fallback.json');
    expect(dispositionFilename(`attachment; filename*=UTF-8''%E0%A4%A`)).toBeNull();
    expect(dispositionFilename('attachment; filename="../../etc/passwd"')).toBe('passwd');
    expect(dispositionFilename('attachment; filename="C:\\\\x\\\\evil.exe"')).toBe('evil.exe');
    expect(dispositionFilename('attachment; filename="a\u0007b.txt"')).toBe('ab.txt');
    expect(dispositionFilename('attachment; filename=".."')).toBeNull();
    expect(dispositionFilename('attachment; filename="dir/"')).toBeNull();
  });
});

describe('apiRelativePath', () => {
  const here = 'http://dash.test/runs/r1?tab=artifacts';
  it('accepts API paths under the base path and strips the base path', async () => {
    const { apiRelativePath } = await import('./endpoints');
    expect(apiRelativePath('', '/v1/runs/r/artifacts/blob/raw/a.eml', here)).toBe('/v1/runs/r/artifacts/blob/raw/a.eml');
    expect(apiRelativePath('http://api.test', '/v1/x', here)).toBe('/v1/x');
    expect(apiRelativePath('http://api.test/prefix/', '/v1/x', here)).toBe('/v1/x');
  });
  it('refuses other origins, paths outside /v1/, dot segments (also encoded), queries and backslashes', async () => {
    const { apiRelativePath } = await import('./endpoints');
    for (const bad of [
      'https://evil.example/v1/x',
      '//evil.example/v1/x',
      'v1/x',
      '/v2/x',
      '/v1/../admin',
      '/v1/%2e%2e/admin',
      '/v1/%2E/x',
      '/v1/x?y=1',
      '/v1/x#y',
      '/v1\\..\\x',
      '/v1',
    ]) {
      expect(() => apiRelativePath('', bad, here), bad).toThrow(/not a file of this API/);
    }
    expect(() => apiRelativePath('http://api.test/prefix', '/v1/x/../../../v1/y', here)).toThrow();
  });
});

describe('isRedacted', () => {
  it('recognises the server placeholder for one-time secrets', () => {
    expect(isRedacted('<redacted: shown once>')).toBe(true);
    expect(isRedacted('msk_abc')).toBe(false);
    expect(isRedacted(null)).toBe(false);
  });
});

describe('apiBlob / apiText', () => {
  it('returns bytes, type and the server filename; errors use the envelope', async () => {
    server.use(
      http.get('http://api.test/v1/file', () =>
        new HttpResponse(new Uint8Array([1, 2, 3]), {
          headers: { 'content-type': 'application/pdf', 'content-disposition': 'attachment; filename="x.pdf"' },
        }),
      ),
      http.get('http://api.test/v1/bare', () => new HttpResponse(new Uint8Array([9]))),
      http.get('http://api.test/v1/gone', () =>
        HttpResponse.json({ error: { code: 'NOT_FOUND', message: 'gone', details: {}, request_id: 'r9' } }, { status: 404 }),
      ),
    );
    const out = await apiBlob(cfg, '/v1/file');
    expect(out.contentType).toBe('application/pdf');
    expect(out.filename).toBe('x.pdf');
    expect(out.blob.size).toBe(3);
    const bare = await apiBlob(cfg, '/v1/bare');
    expect(bare.filename).toBeNull();
    expect(bare.contentType).toBe('application/octet-stream');
    await expect(apiBlob(cfg, '/v1/gone')).rejects.toMatchObject({ code: 'NOT_FOUND', requestId: 'r9' });
    const text = await apiText(cfg, '/v1/file');
    expect(text.filename).toBe('x.pdf');
  });
});

describe('apiRequest', () => {
  it('sends a given idempotency key and honours a per-request timeout', async () => {
    const keys: (string | null)[] = [];
    server.use(
      http.post('http://api.test/v1/idem', async ({ request }) => {
        keys.push(request.headers.get('idempotency-key'));
        await delay(80);
        return HttpResponse.json({});
      }),
    );
    await apiRequest(cfg, 'POST', '/v1/idem', { idempotencyKey: 'dash-fixed' });
    await apiRequest(cfg, 'POST', '/v1/idem', { idempotent: true, idempotencyKey: 'dash-fixed' });
    expect(keys).toEqual(['dash-fixed', 'dash-fixed']);
    await expect(apiRequest({ ...cfg, timeoutMs: 5_000 }, 'POST', '/v1/idem', { timeoutMs: 20 })).rejects.toMatchObject({ kind: 'timeout' });
  });

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

  it('times out while the body is still streaming', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => {
      const signal = (init as RequestInit).signal as AbortSignal;
      return {
        ok: true,
        status: 200,
        headers: new Headers(),
        text: () =>
          new Promise<string>((_resolve, reject) =>
            signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))),
          ),
      } as unknown as Response;
    });
    const err = (await apiRequest({ ...cfg, timeoutMs: 30 }, 'GET', '/v1/stall').catch((e: unknown) => e)) as ApiError;
    expect(err).toMatchObject({ kind: 'timeout', code: 'TIMEOUT' });
  });

  it('rejects non-JSON success bodies and accepts empty ones', async () => {
    server.use(
      http.get('http://api.test/v1/html200', () => new HttpResponse('<html>proxy login</html>', { status: 200, headers: { 'x-request-id': 'h1' } })),
      http.get('http://api.test/v1/empty200', () => new HttpResponse('', { status: 200 })),
    );
    const err = (await apiRequest(cfg, 'GET', '/v1/html200').catch((e: unknown) => e)) as ApiError;
    expect(err).toMatchObject({ kind: 'http', status: 200, code: 'INVALID_RESPONSE', requestId: 'h1' });
    await expect(apiRequest(cfg, 'GET', '/v1/empty200')).resolves.toBeUndefined();
  });
});
