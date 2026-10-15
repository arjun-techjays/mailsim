import { http, HttpResponse } from 'msw';
import { MOCK_KEYS } from '../mocks/db';
import { server } from '../test/server';
import { API_BASE } from '../test/utils';
import { detectRole } from './detectRole';

const cfg = (apiKey: string) => ({ baseUrl: API_BASE, apiKey });

describe('detectRole', () => {
  it.each([
    [MOCK_KEYS.controller, 'controller'],
    [MOCK_KEYS.evaluator, 'evaluator'],
    [MOCK_KEYS.agent, 'agent'],
  ])('%s -> %s', async (key, role) => {
    await expect(detectRole(cfg(key))).resolves.toBe(role);
  });

  it('treats a readable answer-key list as evaluator', async () => {
    server.use(http.get('*/v1/scenarios/:key/answer-keys', () => HttpResponse.json([])));
    await expect(detectRole(cfg(MOCK_KEYS.evaluator))).resolves.toBe('evaluator');
  });

  it('propagates 401 for unknown keys', async () => {
    await expect(detectRole(cfg('bogus'))).rejects.toMatchObject({ status: 401, code: 'UNAUTHENTICATED' });
  });

  it('propagates unexpected probe failures', async () => {
    server.use(
      http.get('*/v1/scenarios/:key/answer-keys', () =>
        HttpResponse.json({ error: { code: 'INTERNAL', message: 'x', details: {}, request_id: 'r' } }, { status: 500 }),
      ),
    );
    await expect(detectRole(cfg(MOCK_KEYS.controller))).rejects.toMatchObject({ status: 500 });
  });

  it('propagates network failures on the first call', async () => {
    server.use(http.get('*/v1/scenarios', () => HttpResponse.error()));
    await expect(detectRole(cfg(MOCK_KEYS.controller))).rejects.toMatchObject({ kind: 'network' });
  });
});
