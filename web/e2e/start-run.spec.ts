import { expect, test } from '@playwright/test';
import { expectAccessible, KEYS } from './fixtures';
import { ConnectPage } from './pages/ConnectPage';
import { ScenariosPage } from './pages/ScenariosPage';

test('start run shows the agent credential exactly once', async ({ page }) => {
  await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
  const scenarios = new ScenariosPage(page);
  await scenarios.open();
  await scenarios.openScenario('finance.invoice-bank-change');
  await page.getByRole('link', { name: 'Start a run' }).click();

  await page.getByLabel('Scenario version').selectOption('2');
  await page.getByRole('radio', { name: 'Off' }).check();
  await page.getByLabel('Agent build id').fill('agent-v9');
  await page.getByRole('button', { name: 'Create run' }).click();

  const panel = page.getByTestId('agent-credentials');
  await expect(panel).toBeVisible();
  await expect(panel.getByRole('alert')).toContainText('will not be shown again');
  const key = await panel.getByTestId('agent-api-key').inputValue();
  expect(key).toMatch(/^mk_agent_run_\d+_finance$/);
  await expectAccessible(page, 'credential panel');

  await panel.getByRole('button', { name: 'Copy key' }).click();
  await expect(panel.getByText('Copied to clipboard.')).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(key);

  // never persisted anywhere
  const storage = await page.evaluate(() => JSON.stringify({ ...window.localStorage, ...window.sessionStorage }));
  expect(storage).not.toContain(key);

  await panel.getByRole('button', { name: /hide it/ }).click();
  await expect(page.getByTestId('agent-credentials')).toHaveCount(0);
  await expect(page.getByText(key)).toHaveCount(0);

  await page.getByRole('link', { name: /Open run run_/ }).click();
  await expect(page.getByTestId('run-summary')).toContainText('RUNNING');
  await expect(page.getByTestId('run-summary')).toContainText('agent-v9');

  await page.goBack();
  await expect(page.getByRole('heading', { name: 'Start a run' })).toBeVisible();
  await expect(page.getByTestId('agent-credentials')).toHaveCount(0);
});
