import { expect, test } from '@playwright/test';
import { expectAccessible, KEYS } from './fixtures';
import { ConnectPage } from './pages/ConnectPage';
import { ScenariosPage } from './pages/ScenariosPage';

test.describe('scenarios', () => {
  test('lists scenarios and filters them', async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
    const scenarios = new ScenariosPage(page);
    await scenarios.open();
    await expect(scenarios.rows()).toHaveCount(3); // header + 2
    await expect(page.getByRole('link', { name: 'finance.invoice-bank-change' })).toBeVisible();
    await expectAccessible(page, 'scenarios');

    await scenarios.filter({ tag: 'support' });
    await expect(scenarios.rows()).toHaveCount(2);
    await expect(page).toHaveURL(/tag=support/);

    await scenarios.filter({ tag: '', q: 'nothing-matches' });
    await expect(page.getByTestId('empty-state')).toHaveText('No scenarios match these filters.');
  });

  test('shows the empty state for a project without scenarios', async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.controllerEmpty, 'controller');
    await new ScenariosPage(page).open();
    await expect(page.getByTestId('empty-state')).toHaveText('No scenarios have been published in this project yet.');
  });

  test('scenario detail shows versions; answer keys only for evaluators', async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
    const scenarios = new ScenariosPage(page);
    await scenarios.open();
    await scenarios.openScenario('finance.invoice-bank-change');
    await expect(page.getByTestId('scenario-versions').getByRole('listitem')).toHaveText(['v1', 'v2', 'v3']);
    await expect(page.getByRole('link', { name: 'Start a run' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Answer-key versions' })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Compare builds' })).toHaveCount(0);

    await new ConnectPage(page).connectAs(KEYS.evaluator, 'evaluator');
    await page.goto('/scenarios/finance.invoice-bank-change');
    await expect(page.getByTestId('answer-keys').getByRole('row')).toHaveCount(3);
    await expect(page.getByRole('link', { name: 'Start a run' })).toHaveCount(0);
    await expectAccessible(page, 'scenario detail (evaluator)');
  });
});
