import { expect, test } from '@playwright/test';
import { KEYS } from './fixtures';
import { ConnectPage } from './pages/ConnectPage';
import { RunPage } from './pages/RunPage';

test.describe('API failure states', () => {
  test.beforeEach(async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
  });

  test('500 error envelope with request id', async ({ page }) => {
    await page.goto('/scenarios?q=boom');
    const err = page.getByTestId('api-error');
    await expect(err).toContainText('Could not load scenarios: INTERNAL (HTTP 500)');
    await expect(err).toContainText('internal error');
    await expect(err.getByTestId('request-id')).toHaveText(/^req_mock_\d+$/);

    await new RunPage(page).goto('run_boom');
    await expect(page.getByTestId('api-error')).toContainText('INTERNAL (HTTP 500)');
  });

  test('timeout state', async ({ page }) => {
    await page.goto('/scenarios?q=slow');
    await expect(page.getByRole('status')).toContainText('Loading scenarios');
    const err = page.getByTestId('api-error');
    await expect(err).toContainText('Request timed out', { timeout: 15_000 });
    await expect(err).toContainText('did not respond within 4s');
  });

  test('unknown run is a 404 envelope', async ({ page }) => {
    await new RunPage(page).goto('run_missing');
    await expect(page.getByTestId('api-error')).toContainText('NOT_FOUND (HTTP 404)');
  });
});
