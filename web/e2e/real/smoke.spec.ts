import { expect, test, type Page } from '@playwright/test';
import { expectAccessible } from '../fixtures';
import { ComparePage } from '../pages/ComparePage';
import { ConnectPage } from '../pages/ConnectPage';
import { EvaluationPage } from '../pages/EvaluationPage';
import { RunPage } from '../pages/RunPage';

// Real-stack tests (no MSW). Run with `npm run e2e:real` and:
//   MAILSIM_API_URL=http://127.0.0.1:8000  (Vite proxies /v1 there)
//   MAILSIM_CONTROLLER_KEY / MAILSIM_EVALUATOR_KEY / MAILSIM_AGENT_KEY (tests needing a missing key skip)
//   MAILSIM_SCENARIO_KEY (default finance.invoice-bank-change; must be published with an answer key)
// Non-destructive: only creates one run (via the UI) and evaluations of that run.
const API = process.env.MAILSIM_API_URL;
const SCENARIO = process.env.MAILSIM_SCENARIO_KEY ?? 'finance.invoice-bank-change';
const keys = {
  controller: process.env.MAILSIM_CONTROLLER_KEY,
  evaluator: process.env.MAILSIM_EVALUATOR_KEY,
  agent: process.env.MAILSIM_AGENT_KEY,
};

test.skip(!API, 'MAILSIM_API_URL is not set; real-stack e2e skipped');
test.describe.configure({ mode: 'serial' });

function trackApi(page: Page): string[] {
  const calls: string[] = [];
  page.on('request', (r) => {
    const url = new URL(r.url());
    if (url.pathname.startsWith('/v1/')) calls.push(`${r.method()} ${url.pathname}`);
  });
  return calls;
}

let runId = '';

test('health endpoint is reachable through the dev proxy', async ({ request }) => {
  const res = await request.get('/v1/health');
  expect(res.ok()).toBe(true);
  expect(await res.json()).toMatchObject({ status: 'ok' });
});

for (const role of ['controller', 'evaluator', 'agent'] as const) {
  test(`connect detects the ${role} role via GET /v1/me`, async ({ page }) => {
    const key = keys[role];
    test.skip(!key, `MAILSIM_${role.toUpperCase()}_KEY not set`);
    await new ConnectPage(page).goto();
    const calls = trackApi(page);
    await new ConnectPage(page).connect(key as string);
    await expect(page.getByTestId('detected-role')).toHaveText(role);
    // no probing fallback needed (the header status pill may also ask /v1/system/status)
    expect(calls.filter((c) => c !== 'GET /v1/system/status')).toEqual(['GET /v1/me']);
    await expectAccessible(page, `connect (${role})`);
  });
}

test('agent key gets the explanation page and no data requests', async ({ page }) => {
  test.skip(!keys.agent, 'MAILSIM_AGENT_KEY not set');
  await new ConnectPage(page).connectAs(keys.agent as string, 'agent');
  const calls = trackApi(page);
  for (const path of ['/scenarios', '/runs', '/']) {
    await page.goto(path);
    await expect(page.getByTestId('agent-key-notice')).toBeVisible();
  }
  expect(calls).toEqual([]);
});

test('controller: scenarios, start a run (credential once), runs list', async ({ page }) => {
  test.skip(!keys.controller, 'MAILSIM_CONTROLLER_KEY not set');
  await new ConnectPage(page).connectAs(keys.controller as string, 'controller');
  await page.getByRole('link', { name: 'Continue' }).click();
  await expect(page.getByRole('link', { name: SCENARIO })).toBeVisible();
  await expectAccessible(page, 'scenarios (real)');

  await page.getByRole('link', { name: SCENARIO }).click();
  await page.getByRole('link', { name: 'Start a run' }).click();
  await page.getByLabel('Agent build id').fill('e2e-real');
  await page.getByRole('button', { name: 'Create run' }).click();
  const panel = page.getByTestId('agent-credentials');
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId('agent-api-key')).not.toHaveValue('');
  const link = panel.getByRole('link', { name: /Go to run/ });
  runId = ((await link.textContent()) ?? '').replace('Go to run ', '').trim();
  expect(runId).toMatch(/^run_/);
  await panel.getByRole('button', { name: /hide it/ }).click();
  await expect(page.getByTestId('agent-credentials')).toHaveCount(0);

  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Runs' }).click();
  const table = page.getByTestId('runs-table');
  await expect(table.getByRole('link', { name: runId })).toBeVisible();
  await expect(table.getByRole('row', { name: new RegExp(runId) })).toContainText('e2e-real');
});

test('controller: run detail, advance clock, trace and state mailbox inspector', async ({ page }) => {
  test.skip(!keys.controller || !runId, 'needs MAILSIM_CONTROLLER_KEY and the run from the previous test');
  await new ConnectPage(page).connectAs(keys.controller as string, 'controller');
  const run = new RunPage(page);
  await run.goto(runId);
  await expect(run.summary()).toContainText('RUNNING');
  const before = await page.getByTestId('run-virtual-time').textContent();
  await run.advance(60);
  await expect(run.controlResult()).toContainText('Clock advanced: virtual time is now');
  await expect(page.getByTestId('persona-replies')).toBeVisible();
  await expect(page.getByTestId('run-virtual-time')).not.toHaveText(before ?? '');
  await expectAccessible(page, 'run overview (real)');

  await run.tab('Event trace');
  const rows = page.getByTestId('event-table').locator('tbody tr');
  await expect(rows.first()).toBeVisible();
  await page.getByLabel('Type').selectOption('run.created');
  await expect(rows).toHaveCount(1);

  await run.tab('Mailboxes');
  // mailbox browser (UX-F2): the agent's mailbox is listed with its badge
  const nav = page.getByRole('navigation', { name: 'Mailboxes and folders' });
  await expect(nav.getByRole('button', { name: /finance@example\.test/ })).toContainText('Agent under test');
  await run.mailbox('Finance Manager');
  const first = run.messageList().locator('tbody tr').first();
  await first.getByRole('button').first().click();
  const view = page.getByTestId('message-view');
  await expect(view).toBeVisible();
  await view.getByRole('button', { name: 'Show headers' }).click();
  await expect(view.getByTestId('message-headers')).toContainText('Message-ID');
  await expect(view.getByTestId('thread')).toBeVisible();
  await expectAccessible(page, 'mailbox inspector (real)');
});

test('evaluator: trigger an evaluation and read the report with per-assertion diff', async ({ page }) => {
  test.skip(!keys.evaluator || !runId, 'needs MAILSIM_EVALUATOR_KEY and the run from the earlier test');
  await new ConnectPage(page).connectAs(keys.evaluator as string, 'evaluator');
  const ev = new EvaluationPage(page);
  await ev.goto(runId);
  await ev.trigger({ checks: 'Run / answer-key default' });
  const report = ev.report();
  await expect(report).toBeVisible();
  await expect(report).toContainText(/PASSED|FAILED|INCONCLUSIVE/);
  const outcomes = page.getByTestId('assertions-table').locator(':scope > tbody > tr');
  await expect(outcomes.first()).toBeVisible();
  await outcomes.first().getByText('Expected vs actual').click();
  await expect(outcomes.first()).toContainText('Matched:');
  await expectAccessible(page, 'evaluation report (real)');

  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: 'Download Markdown' }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/\.md$/);
});

test('evaluator: build comparison', async ({ page }) => {
  test.skip(!keys.evaluator, 'MAILSIM_EVALUATOR_KEY not set');
  await new ConnectPage(page).connectAs(keys.evaluator as string, 'evaluator');
  const cmp = new ComparePage(page);
  await cmp.goto(SCENARIO);
  await expect(cmp.table().locator('tbody tr').first()).toBeVisible();
  await expect(page.getByRole('img', { name: /Mean accuracy by build/ })).toBeVisible();
  await expectAccessible(page, 'build comparison (real)');
});
