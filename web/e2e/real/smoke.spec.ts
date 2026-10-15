import { expect, test } from '@playwright/test';
import { expectAccessible } from '../fixtures';
import { ConnectPage } from '../pages/ConnectPage';

// Real-stack smoke tests (no MSW). Run with `npm run e2e:real` and:
//   MAILSIM_API_URL=http://127.0.0.1:8000  (Vite proxies /v1 there)
//   MAILSIM_CONTROLLER_KEY / MAILSIM_EVALUATOR_KEY / MAILSIM_AGENT_KEY (each optional)
const API = process.env.MAILSIM_API_URL;
const keys = {
  controller: process.env.MAILSIM_CONTROLLER_KEY,
  evaluator: process.env.MAILSIM_EVALUATOR_KEY,
  agent: process.env.MAILSIM_AGENT_KEY,
};

test.skip(!API, 'MAILSIM_API_URL is not set; real-stack e2e skipped');

test('health endpoint is reachable through the dev proxy', async ({ request }) => {
  const res = await request.get('/v1/health');
  expect(res.ok()).toBe(true);
  expect(await res.json()).toMatchObject({ status: 'ok' });
});

for (const role of ['controller', 'evaluator', 'agent'] as const) {
  test(`connect detects the ${role} role`, async ({ page }) => {
    const key = keys[role];
    test.skip(!key, `MAILSIM_${role.toUpperCase()}_KEY not set`);
    await new ConnectPage(page).connectAs(key as string, role);
    await expectAccessible(page, `connect (${role})`);
  });
}

test('controller can list scenarios', async ({ page }) => {
  test.skip(!keys.controller, 'MAILSIM_CONTROLLER_KEY not set');
  await new ConnectPage(page).connectAs(keys.controller as string, 'controller');
  await page.getByRole('link', { name: 'Continue' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Scenarios' })).toBeVisible();
  await expect(page.getByRole('status')).toHaveCount(0, { timeout: 15_000 });
  await expect(page.getByTestId('api-error')).toHaveCount(0);
  await expectAccessible(page, 'scenarios (real)');
});

test('agent key is refused on control pages', async ({ page }) => {
  test.skip(!keys.agent, 'MAILSIM_AGENT_KEY not set');
  await new ConnectPage(page).connectAs(keys.agent as string, 'agent');
  await page.goto('/scenarios');
  await expect(page.getByTestId('api-error')).toContainText('PERMISSION_DENIED');
});
