import { expect, test } from '@playwright/test';
import { expectAccessible, KEYS } from './fixtures';
import { ConnectPage } from './pages/ConnectPage';
import { RunPage } from './pages/RunPage';

test.describe('role differences', () => {
  test('controller: evaluator-only views are hidden and gated', async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
    let apiRequests = 0;
    let evalRequests = 0;
    page.on('request', (r) => {
      if (r.url().includes('/v1/')) apiRequests += 1;
      if (/\/v1\/.*(evaluations|answer-keys|accuracy)/.test(r.url())) evalRequests += 1;
    });
    const run = new RunPage(page);
    await run.goto('run_demo');
    await expect(page.getByRole('heading', { name: 'Run controls' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Evaluations' })).toHaveCount(0);
    expect(apiRequests).toBeGreaterThan(0); // the listener sees mocked API calls
    await page.goto('/runs/run_demo/evaluations');
    await expect(page.getByTestId('role-gate')).toContainText('Evaluation results requires an evaluator key');
    await page.goto('/scenarios/finance.invoice-bank-change/compare');
    await expect(page.getByTestId('role-gate')).toContainText('Build comparison');
    expect(evalRequests).toBe(0);
    await expectAccessible(page, 'role gate');
  });

  test('agent: control pages are refused with a 403 message', async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.agent, 'agent');
    const nav = page.getByRole('navigation', { name: 'Main' });
    await expect(nav.getByRole('link', { name: 'Scenarios' })).toHaveCount(0);
    await expect(nav.getByRole('link', { name: 'My mailbox' })).toBeVisible();

    await page.goto('/scenarios');
    const err = page.getByTestId('api-error');
    await expect(err).toContainText('PERMISSION_DENIED (HTTP 403)');
    await expect(err).toContainText('role is not allowed');

    await new RunPage(page).goto('run_demo');
    await expect(page.getByTestId('api-error')).toContainText('PERMISSION_DENIED (HTTP 403)');
    await expect(page.getByRole('heading', { name: 'Run controls' })).toHaveCount(0);
    await expectAccessible(page, 'agent 403');
  });

  test('agent: own mailbox via the agent API with server-side cursor pagination', async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.agent, 'agent');
    await page.getByRole('link', { name: 'Continue' }).click();
    await page.getByLabel('Mailbox id').fill('mbx_finance');
    await page.getByRole('button', { name: 'Open mailbox' }).click();
    const list = page.getByTestId('message-list');
    await expect(list.locator('tbody tr')).toHaveCount(20);
    await page.getByRole('button', { name: 'Load more messages' }).click();
    await expect(list.locator('tbody tr')).toHaveCount(25);
    await list.getByRole('button', { name: 'Invoice 4471', exact: true }).click();
    await expect(page.getByTestId('message-view')).toContainText('invoice-4471.pdf');
    await expectAccessible(page, 'agent mailbox');

    await page.goto('/mailbox/mbx_vendor');
    await expect(page.getByTestId('api-error').first()).toContainText('PERMISSION_DENIED');
  });
});
