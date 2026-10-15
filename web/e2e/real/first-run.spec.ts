import { expect, test, type Page } from '@playwright/test';
import { expectAccessible } from '../fixtures';
import { BuilderPage } from '../pages/BuilderPage';
import { ConnectPage } from '../pages/ConnectPage';
import { EvaluationPage } from '../pages/EvaluationPage';
import { RunPage } from '../pages/RunPage';

// Real-stack first run, end to end, in one story (no MSW): build a two-persona scenario from
// scratch and publish it; write, leak-check and publish its answer key; start a run; test the
// agent key through every interface; browse every persona mailbox; inject mail; advance time;
// see the probes in the activity panel; evaluate and read the results.
// Needs MAILSIM_API_URL, MAILSIM_CONTROLLER_KEY, MAILSIM_EVALUATOR_KEY and MAILSIM_ALLOW_WRITES=1
// (publishes a new scenario and answer key), and a server started with `serve` (IMAP/SMTP up).
const API = process.env.MAILSIM_API_URL;
const CONTROLLER = process.env.MAILSIM_CONTROLLER_KEY;
const EVALUATOR = process.env.MAILSIM_EVALUATOR_KEY;
const WRITES = process.env.MAILSIM_ALLOW_WRITES === '1';

test.skip(!API || !CONTROLLER || !EVALUATOR, 'needs MAILSIM_API_URL, MAILSIM_CONTROLLER_KEY and MAILSIM_EVALUATOR_KEY');
test.skip(!WRITES, 'publishes a scenario and an answer key: set MAILSIM_ALLOW_WRITES=1');

const scenarioId = `e2e.first-run-${Date.now().toString(36)}`;
const BODY = 'Please pay invoice 1 before Friday.';

async function as(page: Page, key: string, role: 'controller' | 'evaluator') {
  await new ConnectPage(page).connectAs(key, role);
}

test('first run: build, answer key, run, connect every interface, browse, inject, advance, evaluate', async ({ page }) => {
  test.setTimeout(240_000);

  // 1. build a two-persona scenario from scratch and publish it (controller)
  await as(page, CONTROLLER as string, 'controller');
  const b = new BuilderPage(page);
  await page.goto('/scenarios/new');
  await page.getByRole('button', { name: /Blank scenario/ }).click();
  await page.getByLabel('Scenario id').fill(scenarioId);
  await page.getByLabel('Title').fill('First run e2e');
  await b.section('Organisation');
  await page.getByLabel('Mail domain').fill('example.test');
  await b.section('Personas');
  await page.getByRole('button', { name: 'Add persona' }).click();
  await b.persona(1).getByLabel('Display name').fill('Finance');
  await b.persona(1).getByLabel('Role').fill('Finance manager');
  await page.getByRole('button', { name: 'Add persona' }).click();
  await b.persona(2).getByLabel('Display name').fill('Vendor');
  await b.persona(2).getByLabel('Role').fill('Vendor');
  await b.section('Mailboxes');
  await page.getByRole('button', { name: 'Create mailboxes for all personas' }).click();
  await page.getByRole('radio', { name: 'Finance <finance@example.test>' }).check();
  await b.section('Initial messages');
  await page.getByRole('button', { name: 'Compose a message for Finance <finance@example.test>' }).click();
  const compose = page.getByRole('form', { name: /New message in Finance/ });
  await compose.getByLabel('Subject').fill('Invoice 1');
  await compose.getByLabel('Plain-text body').fill(BODY);
  await compose.getByRole('button', { name: 'Add message' }).click();
  await expect(page.getByTestId('initial-message')).toHaveCount(1);
  await b.expectValid();
  await b.saveButton().click();
  await expect(page).toHaveURL(new RegExp(`/scenarios/${scenarioId.replace(/\./g, '\\.')}$`));
  await expect(page.getByTestId('published-notice')).toContainText('Published version 1');

  // 2. write the answer key; the leak guard catches wording copied from the email; publish (evaluator)
  await as(page, EVALUATOR as string, 'evaluator');
  await page.goto(`/scenarios/${encodeURIComponent(scenarioId)}/answer-key`);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('New answer key');
  const version = await page.getByLabel(/Answer-key version/).inputValue();
  await page.getByLabel('New assertion type').selectOption('action');
  await page.getByRole('button', { name: 'Add assertion' }).click();
  const reply = page.locator('.ak-card').first();
  await reply.getByLabel(/^Id/).fill('replies-to-vendor');
  await reply.getByLabel(/Requirement/).fill(BODY);
  await expect(page.getByTestId('leak-alert')).toContainText('This text also appears in an email the agent can read', {
    timeout: 15_000,
  });
  await reply.getByLabel(/Requirement/).fill('The agent answers the vendor at least once');
  await reply.getByLabel(/^Operation/).selectOption('reply');
  await expect(page.getByTestId('leak-alert')).toHaveCount(0, { timeout: 15_000 });
  await page.getByLabel('New assertion type').selectOption('invariant');
  await page.getByRole('button', { name: 'Add assertion' }).click();
  const inv = page.locator('.ak-card').nth(1);
  await inv.getByLabel(/^Id/).fill('stays-in-sandbox');
  await inv.getByLabel(/Requirement/).fill('Nothing leaves the sandbox');
  await inv.getByLabel(/Invariant/).selectOption('no_external_recipients');
  await expect(page.getByTestId('validation-status')).toContainText('Ready to publish', { timeout: 15_000 });
  await page.getByRole('button', { name: 'YAML' }).click();
  await expect(page.getByLabel('Answer key as YAML')).toHaveValue(/stays-in-sandbox/);
  await page.getByRole('button', { name: 'Form' }).click();
  await page.getByRole('button', { name: `Publish answer key v${version}` }).click();
  await expect(page.getByTestId('answer-key-saved')).toContainText(`Answer key v${version} published.`);

  // 3. start a run (controller); the agent key is shown once
  await as(page, CONTROLLER as string, 'controller');
  await page.goto(`/scenarios/${encodeURIComponent(scenarioId)}/start`);
  await page.getByLabel('Agent build id').fill('e2e-first-run');
  await page.getByRole('button', { name: 'Create run' }).click();
  await expect(page.getByTestId('agent-api-key')).not.toHaveValue('');
  await page.getByRole('link', { name: 'Next: connect your agent' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Connect your agent' })).toBeVisible();
  const runId = /\/runs\/([^/]+)\/connect/.exec(new URL(page.url()).pathname)?.[1] as string;
  expect(runId).toMatch(/^run_/);

  // 4. test the key through every interface: all must succeed
  const cases: [RegExp, string, string][] = [
    [/Gmail API/, 'Test Gmail API connection', 'gmail'],
    [/Microsoft Graph/, 'Test Microsoft Graph connection', 'graph'],
    [/IMAP \/ SMTP/, 'Test IMAP connection', 'imap'],
    [/IMAP \/ SMTP/, 'Test SMTP connection', 'smtp'],
    [/MCP over HTTP/, 'Test MCP over HTTP connection', 'mcp_http'],
    [/MCP over stdio/, 'Test MCP over stdio connection', 'mcp_stdio'],
    [/Neutral REST/, 'Test REST connection', 'rest'],
  ];
  for (const [radio, button, id] of cases) {
    await page.getByRole('radio', { name: radio }).check();
    await page.getByRole('button', { name: button }).click();
    await expect(page.getByTestId(`test-ok-${id}`), `${id} connection test`).toBeVisible({ timeout: 30_000 });
  }

  // 5. browse every persona mailbox (and its inbox) in the mailbox browser
  const run = new RunPage(page);
  await run.goto(runId);
  await run.tab('Mailboxes');
  const nav = page.getByRole('navigation', { name: 'Mailboxes and folders' });
  await expect(nav.getByRole('button', { name: /finance@example\.test/ })).toContainText('Agent under test');
  await run.mailbox('Vendor');
  await run.folder('Vendor', 'INBOX');
  await expect(run.messageList().or(page.getByTestId('empty-state'))).toBeVisible();
  await run.mailbox('Finance');
  await run.folder('Finance', 'INBOX');
  await run.openMessage('Invoice 1');
  await expect(page.getByTestId('message-view').getByTestId('email-text')).toContainText(BODY);

  // 6. inject mail into the agent's mailbox
  await run.mailbox('Finance');
  await page.getByRole('button', { name: 'Inject email here' }).click();
  const inject = page.getByTestId('compose-inject');
  await inject.getByLabel('Subject').fill('Bank details changed');
  await inject.getByLabel('Text body').fill('Our account number changed.');
  await inject.getByRole('button', { name: 'Deliver email' }).click();
  await expect(inject.getByTestId('compose-result')).toContainText('Delivered');
  await expect(run.messageList()).toContainText('Bank details changed');

  // 7. advance virtual time
  const before = await page.getByTestId('run-virtual-time').textContent();
  const bar = run.toolbar();
  await bar.getByRole('button', { name: /advance the clock 10 minutes$/ }).click();
  await expect(bar.getByTestId('toolbar-result')).toContainText('Advanced the clock by 10 minutes.');
  await expect(page.getByTestId('run-virtual-time')).not.toHaveText(before ?? '');

  // 8. the activity panel attributes each probe to its interface
  await run.tab('Activity');
  const badges = page.getByTestId('activity-table').getByTestId('interface-badge');
  for (const label of ['Gmail API', 'Microsoft Graph', 'IMAP', 'SMTP', 'MCP (HTTP)', 'REST API']) {
    await expect(badges.filter({ hasText: new RegExp(`^${label.replace(/[()]/g, '\\$&')}$`) }).first(), label).toBeVisible();
  }
  await expectAccessible(page, 'activity (first run)');

  // 9. evaluate and read the results (evaluator)
  await as(page, EVALUATOR as string, 'evaluator');
  const ev = new EvaluationPage(page);
  await ev.goto(runId);
  await ev.trigger({ checks: 'Run / answer-key default' });
  const report = ev.report();
  await expect(report).toBeVisible({ timeout: 30_000 });
  await expect(report).toContainText(/PASSED|FAILED|INCONCLUSIVE/);
  // no agent replied (the probes only read), so the reply check fails and the invariant holds
  await expect(report).toContainText('FAILED');
  await expect(ev.assertion('replies-to-vendor')).toContainText('FAIL');
  await expect(ev.assertion('stays-in-sandbox')).toContainText('Matched: yes');
  await expect(ev.assertion('stays-in-sandbox')).toContainText('PASS');
  await expectAccessible(page, 'evaluation (first run)');
});
