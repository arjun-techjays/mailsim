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

  test('agent keys: explanatory page and no data requests', async ({ page }) => {
    const connect = new ConnectPage(page);
    await connect.connectAs(KEYS.agent, 'agent');
    const apiCalls: string[] = [];
    page.on('request', (r) => {
      if (r.url().includes('/v1/')) apiCalls.push(r.url());
    });
    const nav = page.getByRole('navigation', { name: 'Main' });
    await expect(nav.getByRole('link', { name: 'Scenarios' })).toHaveCount(0);
    await page.getByRole('link', { name: 'Continue' }).click();
    await expect(page.getByTestId('agent-key-notice')).toContainText('belong to the agent under test');

    for (const path of ['/scenarios', '/runs/run_demo', '/runs/run_demo/evaluations', '/runs']) {
      await page.goto(path);
      await expect(page.getByTestId('agent-key-notice')).toBeVisible();
    }
    expect(apiCalls).toEqual([]);
    await expectAccessible(page, 'agent key notice');
  });
});
