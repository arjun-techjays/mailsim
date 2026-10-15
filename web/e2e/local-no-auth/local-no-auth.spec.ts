import { expect, test } from '@playwright/test';
import { expectAccessible, KEYS } from '../fixtures';

// Mock API in local no-auth mode (VITE_MOCK_LOCAL_NO_AUTH=1), mirroring `serve --local-no-auth`.
test.describe('local no-auth mode', () => {
  test('no key screen: the dashboard opens as the combined controller + evaluator operator', async ({ page }) => {
    const authHeaders: (string | undefined)[] = [];
    page.on('request', (r) => {
      if (r.url().includes('/v1/')) authHeaders.push(r.headers()['authorization']);
    });
    await page.goto('/');
    await expect(page).toHaveURL(/\/scenarios$/);
    await expect(page.getByTestId('session-role')).toHaveText('operator (local no-auth)');
    await expect(page.getByTestId('local-no-auth-banner')).toContainText('Agent interfaces still require agent keys');
    await expect(page.getByRole('button', { name: 'Disconnect' })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'Connect to mailsim' })).toHaveCount(0);
    await expectAccessible(page, 'operator scenarios');

    // controller + evaluator affordances together
    await page.goto('/scenarios/finance.invoice-bank-change');
    await expect(page.getByRole('link', { name: 'Start a run' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Compare builds' })).toBeVisible();
    await expect(page.getByTestId('answer-keys')).toBeVisible();

    await page.goto('/runs/run_demo');
    await expect(page.getByRole('heading', { name: 'Run controls' })).toBeVisible();
    await page.getByRole('link', { name: 'Evaluations' }).click();
    await expect(page.getByTestId('evaluations-table')).toBeVisible();
    await expect(page.getByTestId('role-gate')).toHaveCount(0);

    expect(authHeaders.length).toBeGreaterThan(0);
    expect(authHeaders.every((h) => h === undefined)).toBe(true); // no key is ever sent
  });

  test('a reload re-detects the mode (nothing is stored)', async ({ page }) => {
    await page.goto('/runs');
    await expect(page.getByTestId('session-role')).toHaveText('operator (local no-auth)');
    expect(await page.evaluate(() => sessionStorage.getItem('mailsim.session'))).toBeNull();
    await page.reload();
    await expect(page.getByTestId('session-role')).toHaveText('operator (local no-auth)');
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
});
