import { expect, test } from '@playwright/test';
import { expectAccessible, KEYS } from './fixtures';
import { ComparePage } from './pages/ComparePage';
import { ConnectPage } from './pages/ConnectPage';

test('build comparison: table, deltas, chart and per-assertion rates', async ({ page }) => {
  await new ConnectPage(page).connectAs(KEYS.evaluator, 'evaluator');
  await page.goto('/scenarios/finance.invoice-bank-change');
  await page.getByRole('link', { name: 'Compare builds' }).click();
  const cmp = new ComparePage(page);
  await expect(cmp.table().locator('tbody tr')).toHaveCount(3);
  await expect(cmp.table().getByRole('row', { name: /agent-v1/ })).toContainText('baseline');
  await expect(cmp.table().getByRole('row', { name: /agent-v2/ })).toContainText('+19.0 pp');
  await expect(cmp.table().getByRole('row', { name: /agent-v3/ })).toContainText('n/a'); // stdev with 1 run
  const chart = page.getByRole('img', { name: /Mean accuracy by build/ });
  await expect(chart).toBeVisible();
  await expect(chart.getByTestId('chart-bar')).toHaveCount(3);
  await expect(page.getByTestId('per-assertion-table')).toContainText('no-unverified-approval');
  await expectAccessible(page, 'build comparison');

  await cmp.compare({ builds: 'agent-v2, agent-v3', baseline: 'agent-v2' });
  await expect(cmp.table().locator('tbody tr')).toHaveCount(2);
  await expect(cmp.table().getByRole('row', { name: /agent-v3/ })).toContainText('-31.0 pp');

  await cmp.compare({ builds: 'agent-v404', baseline: '' });
  await expect(page.getByTestId('api-error')).toContainText('INVALID_REQUEST (HTTP 400)');
});
