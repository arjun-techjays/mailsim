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

test('a slow create and a slow evaluation are recovered without duplicates (UI-8/UI-9)', async ({ page }) => {
  // Mock fault: build "slow" does the work but never answers the first attempt of a key
  // (VITE_LONG_REQUEST_TIMEOUT_MS=4000 in mock mode).
  await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
  const keys: string[] = [];
  page.on('request', (r) => {
    if (r.method() === 'POST' && /\/v1\/scenarios\/[^/]+\/runs$|\/evaluations$/.test(new URL(r.url()).pathname)) {
      keys.push(r.headers()['idempotency-key'] ?? '');
    }
  });
  await page.goto('/scenarios/finance.invoice-bank-change/start');
  await expect(page.getByLabel('Agent build id')).toBeVisible();
  await page.getByLabel('Agent build id').fill('slow');
  await page.getByRole('button', { name: 'Create run' }).click();
  await expect(page.getByText(/seeding its mailboxes/)).toBeVisible();
  const notice = page.getByTestId('unconfirmed');
  await expect(notice).toContainText('The run was not confirmed', { timeout: 10_000 });
  await expect(notice.getByRole('heading', { name: 'The run was not confirmed' })).toBeFocused();
  await expect(page.getByLabel('Agent build id')).toBeDisabled();
  await expectAccessible(page, 'unconfirmed create');
  await notice.getByRole('button', { name: 'Check again' }).click();
  const recovered = page.getByTestId('run-recovered');
  await expect(recovered).toContainText(/Run run_\d+ was created/);
  await expect(page.getByTestId('agent-credentials')).toHaveCount(0);
  expect(keys).toHaveLength(2);
  expect(keys[1]).toBe(keys[0]);
  await recovered.getByRole('button', { name: 'Mint a new agent key' }).click();
  await expect(page.getByTestId('agent-api-key')).toHaveValue(/^msk_/);

  // exactly one run with that build
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Runs' }).click();
  const slowRows = page.getByTestId('runs-table').getByRole('row').filter({ hasText: 'slow' });
  await expect(slowRows).toHaveCount(1);
  const runId = (await slowRows.getByRole('link').first().textContent()) as string;

  // evaluate it as the evaluator (client-side reconnect keeps the mock state)
  await page.getByRole('button', { name: 'Disconnect' }).click();
  await page.getByLabel('API key').fill(KEYS.evaluator);
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.getByTestId('detected-role')).toHaveText('evaluator');
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Runs' }).click();
  await page.getByRole('link', { name: runId, exact: true }).click();
  await page.getByRole('link', { name: 'Evaluations', exact: true }).click();
  await page.getByRole('button', { name: 'Run evaluation' }).click();
  const evalNotice = page.getByTestId('unconfirmed');
  await expect(evalNotice).toContainText('The evaluation was not confirmed', { timeout: 10_000 });
  await evalNotice.getByRole('button', { name: 'Check again' }).click();
  await expect(page.getByTestId('evaluation-report')).toBeVisible();
  await expect(page.getByTestId('evaluations-table').locator('tbody tr')).toHaveCount(1);
  expect(keys).toHaveLength(4);
  expect(keys[3]).toBe(keys[2]);
  expect(keys[2]).not.toBe(keys[0]);
});

test('no compatible answer key: the evaluation refusal lists the keys that exist (EV-4)', async ({ page }) => {
  // Mock fault: build "no-key" -> "latest compatible" answers 409 with details.answer_keys.
  await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
  await page.goto('/scenarios/finance.invoice-bank-change/start');
  await expect(page.getByLabel('Agent build id')).toBeVisible();
  await page.getByLabel('Agent build id').fill('no-key');
  await page.getByRole('button', { name: 'Create run' }).click();
  const runLink = page.getByRole('link', { name: /^Go to run run_/ });
  const runId = ((await runLink.textContent()) as string).replace('Go to run ', '');
  await page.getByRole('button', { name: 'Disconnect' }).click();
  await page.getByLabel('API key').fill(KEYS.evaluator);
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.getByTestId('detected-role')).toHaveText('evaluator');
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Runs' }).click();
  await page.getByRole('link', { name: runId, exact: true }).click();
  await page.getByRole('link', { name: 'Evaluations', exact: true }).click();
  await page.getByRole('button', { name: 'Run evaluation' }).click();
  const conflict = page.getByTestId('evaluation-conflict');
  await expect(conflict).toContainText('No answer key covers scenario v3, the version this run uses');
  await expect(conflict).toContainText('existing: v1 (covers scenario v1, v2); v2 (covers scenario v2)');
  await expect(conflict.getByRole('link', { name: 'write an answer key that covers it' })).toHaveAttribute(
    'href',
    '/scenarios/finance.invoice-bank-change/answer-key',
  );
  await expectAccessible(page, 'evaluation conflict');
});
