import { http, HttpResponse } from 'msw';
import { MOCK_KEYS } from '../mocks/db';
import { server } from '../test/server';
import { API_BASE } from '../test/utils';
import { detectRole } from './detectRole';

const cfg = (apiKey: string) => ({ baseUrl: API_BASE, apiKey });
const envelope = (status: number, code: string) =>
  HttpResponse.json({ error: { code, message: code, details: {}, request_id: 'r' } }, { status });
const meAbsent = (status: number) => server.use(http.get('*/v1/me', () => envelope(status, 'NOT_FOUND')));

const ROLES = [
  [MOCK_KEYS.controller, 'controller'],
  [MOCK_KEYS.evaluator, 'evaluator'],
  [MOCK_KEYS.agent, 'agent'],
] as const;

describe('detectRole via GET /v1/me', () => {
  it.each(ROLES)('%s -> %s', async (key, role) => {
    const seen: string[] = [];
    server.events.on('request:start', ({ request }) => {
      seen.push(new URL(request.url).pathname);
    });
    await expect(detectRole(cfg(key))).resolves.toBe(role);
    expect(seen).toEqual(['/v1/me']); // no probing, no agent-visible calls
    server.events.removeAllListeners();
  });

  it('propagates 401 and unexpected /v1/me failures', async () => {
    await expect(detectRole(cfg('bogus'))).rejects.toMatchObject({ status: 401, code: 'UNAUTHENTICATED' });
    server.use(http.get('*/v1/me', () => envelope(500, 'INTERNAL')));
    await expect(detectRole(cfg(MOCK_KEYS.controller))).rejects.toMatchObject({ status: 500 });
  });

  it('falls back to probing when /v1/me gives no usable role', async () => {
    server.use(http.get('*/v1/me', () => HttpResponse.json({ role: 'admin' })));
    await expect(detectRole(cfg(MOCK_KEYS.controller))).resolves.toBe('controller');
    server.use(http.get('*/v1/me', () => new HttpResponse(null, { status: 204 })));
    await expect(detectRole(cfg(MOCK_KEYS.evaluator))).resolves.toBe('evaluator');
  });
});

describe('detectRole fallback probing (no /v1/me)', () => {
  it.each([404, 405, 501])('/v1/me %i -> probes', async (status) => {
    meAbsent(status);
    for (const [key, role] of ROLES) await expect(detectRole(cfg(key))).resolves.toBe(role);
  });

  it('treats a readable answer-key list as evaluator', async () => {
    meAbsent(404);
    server.use(http.get('*/v1/scenarios/:key/answer-keys', () => HttpResponse.json([])));
    await expect(detectRole(cfg(MOCK_KEYS.evaluator))).resolves.toBe('evaluator');
  });

  it('propagates unexpected probe failures', async () => {
    meAbsent(404);
    server.use(http.get('*/v1/scenarios/:key/answer-keys', () => envelope(500, 'INTERNAL')));
    await expect(detectRole(cfg(MOCK_KEYS.controller))).rejects.toMatchObject({ status: 500 });
  });

  it('propagates network failures on the first probe', async () => {
    meAbsent(501);
    server.use(http.get('*/v1/scenarios', () => HttpResponse.error()));
    await expect(detectRole(cfg(MOCK_KEYS.controller))).rejects.toMatchObject({ kind: 'network' });
  });

  it('propagates network failures on /v1/me', async () => {
    server.use(http.get('*/v1/me', () => HttpResponse.error()));
    await expect(detectRole(cfg(MOCK_KEYS.controller))).rejects.toMatchObject({ kind: 'network' });
  });
});
