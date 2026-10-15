import { expect, test } from '@playwright/test';
import { expectAccessible, KEYS } from './fixtures';
import { ConnectPage } from './pages/ConnectPage';

test.describe('connect + role detection', () => {
  for (const [key, role] of [
    [KEYS.controller, 'controller'],
    [KEYS.evaluator, 'evaluator'],
    [KEYS.agent, 'agent'],
  ] as const) {
    test(`detects the ${role} role`, async ({ page }) => {
      const connect = new ConnectPage(page);
      await connect.connectAs(key, role);
      await expect(page.getByTestId('session-role')).toHaveText(role);
    });
  }

  test('keeps the key out of localStorage and the URL', async ({ page }) => {
    const connect = new ConnectPage(page);
    await connect.connectAs(KEYS.controller, 'controller');
    await page.getByRole('link', { name: 'Continue' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Scenarios' })).toBeVisible();
    const local = await page.evaluate(() => JSON.stringify(window.localStorage));
    expect(local).not.toContain(KEYS.controller);
    expect(page.url()).not.toContain(KEYS.controller);
    const session = await page.evaluate(() => window.sessionStorage.getItem('mailsim.session'));
    expect(session).toContain('controller');
  });

  test('rejects an invalid key with the error envelope and request id', async ({ page }) => {
    const connect = new ConnectPage(page);
    await connect.goto();
    await connect.connect('not-a-key');
    const alert = page.getByTestId('api-error');
    await expect(alert).toContainText('UNAUTHENTICATED (HTTP 401)');
    await expect(alert.getByTestId('request-id')).toHaveText(/^req_mock_\d+$/);
  });

  test('requires a key before calling the API', async ({ page }) => {
    const connect = new ConnectPage(page);
    await connect.goto();
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
    await expect(page.getByText('Enter an API key.')).toBeVisible();
    await expect(page.getByLabel('API key')).toHaveAttribute('aria-invalid', 'true');
  });

  test('disconnect clears the session', async ({ page }) => {
    const connect = new ConnectPage(page);
    await connect.connectAs(KEYS.evaluator, 'evaluator');
    await page.getByRole('button', { name: 'Disconnect' }).click();
    await expect(page.getByTestId('session-info')).toHaveCount(0);
    expect(await page.evaluate(() => window.sessionStorage.getItem('mailsim.session'))).toBeNull();
    await page.goto('/scenarios');
    await expect(page).toHaveURL(/\/connect$/);
  });

  test('connect page is accessible', async ({ page }) => {
    const connect = new ConnectPage(page);
    await connect.connectAs(KEYS.controller, 'controller');
    await expectAccessible(page, 'connect');
  });
});
