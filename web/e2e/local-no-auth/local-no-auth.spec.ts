import { expect, test } from '@playwright/test';
import { expectAccessible, KEYS } from '../fixtures';

// Mock API in local no-auth mode (VITE_MOCK_LOCAL_NO_AUTH=1), mirroring `serve --local-no-auth`:
// the server prints a dashboard link carrying a launch token in the URL fragment.
const LAUNCH = '/#local_token=mock-local-token-0123456789';

test.describe('local no-auth mode', () => {
  test('the launch link opens the dashboard as the combined controller + evaluator operator', async ({ page }) => {
    const sent: { auth?: string; token?: string }[] = [];
    page.on('request', (r) => {
      if (r.url().includes('/v1/')) sent.push({ auth: r.headers()['authorization'], token: r.headers()['x-mailsim-local-token'] });
    });
    await page.goto(LAUNCH);
    await expect(page.getByRole('heading', { level: 1, name: 'Test your email agent' })).toBeVisible();
    expect(new URL(page.url()).hash).toBe(''); // token removed from the address bar
    await expect(page.getByTestId('session-role')).toHaveText('operator (local no-auth)');
    await expect(page.getByTestId('local-no-auth-banner')).toContainText('Agent interfaces still require agent keys');
    await expect(page.getByRole('button', { name: 'Disconnect' })).toHaveCount(0);
    await expectAccessible(page, 'operator home');

    await page.goto('/scenarios/finance.invoice-bank-change');
    await expect(page.getByRole('link', { name: 'Start a run' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Compare builds' })).toBeVisible();
    await expect(page.getByTestId('answer-keys')).toBeVisible();

    await page.goto('/runs/run_demo');
    await expect(page.getByRole('heading', { name: 'Run controls' })).toBeVisible();
    await page.getByRole('link', { name: 'Evaluations' }).click();
    await expect(page.getByTestId('evaluations-table')).toBeVisible();
    await expect(page.getByTestId('role-gate')).toHaveCount(0);

    expect(sent.length).toBeGreaterThan(0);
    expect(sent.every((h) => h.auth === undefined && h.token === 'mock-local-token-0123456789')).toBe(true);
    expect(await page.evaluate(() => window.location.href)).not.toContain('local_token');
  });

  test('without the link: no operator access, and the Connect page says where the link is', async ({ page }) => {
    await page.goto('/runs');
    await expect(page).toHaveURL(/\/connect$/);
    await expect(page.getByTestId('local-no-auth-link')).toContainText('Open the dashboard link the server printed');
    await expect(page.getByTestId('session-role')).toHaveCount(0);
    await expectAccessible(page, 'connect in local mode');
  });

  test('a key can still be used explicitly and bad keys are still rejected', async ({ page }) => {
    await page.goto('/connect');
    await page.getByLabel('API key').fill('mk_not_a_real_key');
    await page.getByRole('button', { name: 'Connect' }).click();
    await expect(page.getByRole('alert')).toContainText('Could not connect');
    await page.getByLabel('API key').fill(KEYS.evaluator);
    await page.getByRole('button', { name: 'Connect' }).click();
    await expect(page.getByTestId('detected-role')).toHaveText('evaluator');
    await expect(page.getByTestId('local-no-auth-banner')).toHaveCount(0);
  });

  test('a reload keeps the operator session of this tab', async ({ page }) => {
    await page.goto(LAUNCH);
    await expect(page.getByTestId('session-role')).toHaveText('operator (local no-auth)');
    await page.goto('/runs');
    await page.reload();
    await expect(page.getByTestId('session-role')).toHaveText('operator (local no-auth)');
  });
});
