import { expect, test, type APIRequestContext } from '@playwright/test';
import { expectAccessible } from '../fixtures';
import { ConnectPage } from '../pages/ConnectPage';
import { RunPage } from '../pages/RunPage';

// Real-stack tests for the answer-key editor, mailbox browser, clock toolbar and activity
// panel (UX-F2). Same environment as smoke.spec.ts, plus:
//   MAILSIM_ALLOW_WRITES=1  also publish an answer key (creates a new, permanent key version)
// They skip until the UX endpoints are implemented (UX-B): GET /v1/meta/authoring must answer 200.
const API = process.env.MAILSIM_API_URL;
const SCENARIO = process.env.MAILSIM_SCENARIO_KEY ?? 'finance.invoice-bank-change';
const CONTROLLER = process.env.MAILSIM_CONTROLLER_KEY;
const EVALUATOR = process.env.MAILSIM_EVALUATOR_KEY;
const WRITES = process.env.MAILSIM_ALLOW_WRITES === '1';

test.skip(!API, 'MAILSIM_API_URL is not set; real-stack e2e skipped');
test.describe.configure({ mode: 'serial' });

async function uxReady(request: APIRequestContext, key: string): Promise<boolean> {
  const res = await request.get('/v1/meta/authoring', { headers: { Authorization: `Bearer ${key}` } });
  return res.ok();
}

let runId = '';

test('answer-key editor: pickers, live validation, YAML round trip (and publish with MAILSIM_ALLOW_WRITES=1)', async ({ page, request }) => {
  test.skip(!EVALUATOR, 'MAILSIM_EVALUATOR_KEY not set');
  test.skip(!(await uxReady(request, EVALUATOR as string)), 'UX endpoints not implemented yet (UX-B); skipped');
  test.setTimeout(120_000);
  await new ConnectPage(page).connectAs(EVALUATOR as string, 'evaluator');
  await page.goto(`/scenarios/${encodeURIComponent(SCENARIO)}/answer-key`);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('New answer key');
  const version = await page.getByLabel(/Answer-key version/).inputValue();

  await page.getByLabel('New assertion type').selectOption('action');
  await page.getByRole('button', { name: 'Add assertion' }).click();
  const card = page.locator('.ak-card').first();
  await card.getByLabel(/^Id/).fill('e2e-replies');
  await card.getByLabel(/Requirement/).fill('The agent sends at least one reply');
  await card.getByLabel(/^Operation/).selectOption('reply');
  await page.getByLabel('New assertion type').selectOption('semantic');
  await page.getByRole('button', { name: 'Add assertion' }).click();
  const sem = page.locator('.ak-card').nth(1);
  await sem.getByLabel(/Requirement/).fill('Stays polite');
  await sem.getByLabel(/Rubric for the LLM judge/).fill('PASS if every sent message is courteous.');
  await expect(page.getByTestId('validation-status')).toContainText('Ready to publish', { timeout: 15_000 });

  await page.getByRole('button', { name: 'YAML' }).click();
  await expect(page.getByLabel('Answer key as YAML')).toHaveValue(/e2e-replies/);
  await page.getByRole('button', { name: 'Form' }).click();
  await expect(page.locator('.ak-card')).toHaveCount(2);
  await expectAccessible(page, 'answer-key editor (real)');

  if (WRITES) {
    await page.getByRole('button', { name: `Publish answer key v${version}` }).click();
    await expect(page.getByTestId('answer-key-saved')).toContainText(`Answer key v${version} published.`);
  }
});

test('mailbox browser, inject, clock toolbar and activity on a new run', async ({ page, request }) => {
  test.skip(!CONTROLLER, 'MAILSIM_CONTROLLER_KEY not set');
  test.skip(!(await uxReady(request, CONTROLLER as string)), 'UX endpoints not implemented yet (UX-B); skipped');
  test.setTimeout(120_000);
  const created = await request.post(`/v1/scenarios/${encodeURIComponent(SCENARIO)}/runs`, {
    headers: { Authorization: `Bearer ${CONTROLLER}`, 'Idempotency-Key': `e2e-uxf2-${Date.now()}` },
    data: { agent_build: 'e2e-uxf2', start: true },
  });
  expect(created.ok()).toBe(true);
  runId = ((await created.json()) as { run: { id: string } }).run.id;

  await new ConnectPage(page).connectAs(CONTROLLER as string, 'controller');
  const run = new RunPage(page);
  await run.goto(runId);
  await run.tab('Mailboxes');
  const nav = page.getByRole('navigation', { name: 'Mailboxes and folders' });
  await expect(nav.getByText('Agent under test')).toHaveCount(1);
  await expect(run.messageList()).toBeVisible();

  await page.getByRole('button', { name: 'Inject email here' }).click();
  const compose = page.getByTestId('compose-inject');
  await compose.getByLabel('Subject').fill('E2E injected');
  await compose.getByLabel('Text body').fill('Hello from the dashboard e2e');
  await compose.getByRole('button', { name: 'Deliver email' }).click();
  await expect(compose.getByTestId('compose-result')).toContainText('Delivered');
  await expect(run.messageList()).toContainText('E2E injected');

  const bar = run.toolbar();
  await bar.getByRole('button', { name: /advance the clock 1 minute$/ }).click();
  await expect(bar.getByTestId('toolbar-result')).toContainText('Advanced the clock by 1 minute.');
  await bar.getByRole('button', { name: 'Tick scheduler' }).click();
  await expect(bar.getByTestId('toolbar-result')).toContainText('Scheduler tick.');
  await expectAccessible(page, 'mailbox browser (real)');

  await run.tab('Activity');
  await expect(page.getByRole('heading', { name: 'Activity' })).toBeVisible();
  await expect(page.getByTestId('activity-status')).toContainText(/Live|Not live/);
});
