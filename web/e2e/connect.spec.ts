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
    await connect.connect('');
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

  test('base URL must be a bare https origin (http only for localhost)', async ({ page }) => {
    const connect = new ConnectPage(page);
    await connect.goto();
    for (const [url, msg] of [
      ['http://api.example.com', 'Use https unless the API runs on localhost.'],
      ['https://user:pw@api.example.com', 'The URL must not contain credentials.'],
      ['https://api.example.com/v1?x=1', 'Enter only the origin (https://api.example.com)'],
      ['javascript:alert(1)', 'Only http and https URLs are allowed.'],
    ]) {
      await connect.setBaseUrl(url);
      await connect.connect(KEYS.controller);
      await expect(page.getByText(msg)).toBeVisible();
      await expect(page.getByLabel('API base URL')).toHaveAttribute('aria-invalid', 'true');
    }
    await connect.setBaseUrl('http://127.0.0.1:5174');
    await connect.connect(KEYS.controller);
    await expect(page.getByTestId('detected-role')).toHaveText('controller');
    await expect(page.getByTestId('connected-origin')).toContainText('differs from this dashboard');
  });

  test('a key revoked mid-session (401) shows a reconnect prompt', async ({ page }) => {
    const connect = new ConnectPage(page);
    await connect.connectAs(KEYS.revocable, 'controller');
    await page.getByRole('link', { name: 'Continue' }).click();
    const banner = page.getByTestId('key-rejected');
    await expect(banner).toContainText('The API rejected your key (401).');
    await expect(page.getByTestId('api-error')).toContainText('UNAUTHENTICATED (HTTP 401)');
    await banner.getByRole('button', { name: 'Reconnect' }).click();
    await expect(page).toHaveURL(/\/connect$/);
    await expect(page.getByTestId('session-info')).toHaveCount(0);
  });

  test('connect page is accessible', async ({ page }) => {
    const connect = new ConnectPage(page);
    await connect.connectAs(KEYS.controller, 'controller');
    await expectAccessible(page, 'connect');
  });
});
