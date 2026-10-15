import { expect, test } from '@playwright/test';
import { expectAccessible } from '../fixtures';
import { BuilderPage } from '../pages/BuilderPage';
import { ConnectPage } from '../pages/ConnectPage';

// Real-stack scenario builder (no MSW). Needs MAILSIM_API_URL and MAILSIM_CONTROLLER_KEY, and
// the UX authoring endpoints (GET /v1/meta/authoring, POST /v1/scenario-drafts/*,
// /v1/tools/*, GET /v1/scenarios/{key}/versions/{v}/document). Until the server implements
// them (UX-B), every test here is skipped with that reason.
// Publishes one new scenario (unique id per run) and one new version of it.
const API = process.env.MAILSIM_API_URL;
const CONTROLLER = process.env.MAILSIM_CONTROLLER_KEY;

test.skip(!API, 'MAILSIM_API_URL is not set; real-stack e2e skipped');
test.skip(!CONTROLLER, 'MAILSIM_CONTROLLER_KEY not set');
test.describe.configure({ mode: 'serial' });

const scenarioId = `e2e.builder-${Date.now().toString(36)}`;

test.beforeAll(async ({ request }) => {
  const res = await request.get('/v1/meta/authoring', { headers: { Authorization: `Bearer ${CONTROLLER}` } });
  test.skip(!res.ok(), `authoring endpoints not available yet (GET /v1/meta/authoring -> ${res.status()})`);
});

test('builds, validates and publishes a two-persona scenario against the real API', async ({ page }) => {
  const b = new BuilderPage(page);
  await new ConnectPage(page).connectAs(CONTROLLER as string, 'controller');
  await page.goto('/scenarios/new');
  await page.getByRole('button', { name: /Blank scenario/ }).click();
  await expect(b.status()).toHaveText(/problems to fix/, { timeout: 10_000 });

  await page.getByLabel('Scenario id').fill(scenarioId);
  await page.getByLabel('Title').fill('Builder real-stack e2e');
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
  await compose.getByLabel('Plain-text body').fill('Please pay invoice 1.');
  await compose.getByLabel('Attachments').setInputFiles({ name: 'invoice.txt', mimeType: 'text/plain', buffer: Buffer.from('INV 1') });
  await compose.getByRole('button', { name: 'Add message' }).click();
  await expect(page.getByTestId('initial-message')).toHaveCount(1);
  await page.getByRole('button', { name: 'Compose a message for Finance <finance@example.test>' }).click();
  const reply = page.getByRole('form', { name: /New message in Finance/ });
  await reply.getByLabel('Reply to an earlier message (threading)').selectOption({ index: 1 });
  await reply.getByLabel('Plain-text body').fill('Reminder.');
  await reply.getByRole('button', { name: 'Add message' }).click();
  await expect(page.getByTestId('initial-message').nth(1)).toContainText('In reply to');

  await b.section('Events');
  await page.getByRole('button', { name: 'Add event' }).click();
  await b.event(1).getByLabel('Subject').fill('Following up');
  await b.event(1).getByLabel('Body').fill('Any news?');

  await b.expectValid();
  await page.getByRole('button', { name: 'Show YAML' }).click();
  await b.yamlContains(`id: ${scenarioId}`);
  await expectAccessible(page, 'builder (real)');
  await b.saveButton().click();
  await expect(page).toHaveURL(new RegExp(`/scenarios/${scenarioId.replace(/\./g, '\\.')}$`));
  await expect(page.getByTestId('published-notice')).toContainText('Published version 1');
});

test('edits the published scenario into version 2 (document endpoint round trip)', async ({ page }) => {
  const b = new BuilderPage(page);
  await new ConnectPage(page).connectAs(CONTROLLER as string, 'controller');
  await page.goto(`/scenarios/${scenarioId}/edit?from=1`);
  await expect(page.getByLabel('Version')).toHaveValue('2');
  await b.section('Initial messages');
  await expect(page.getByTestId('initial-message')).toHaveCount(2);
  await expect(page.getByTestId('initial-message').first()).toContainText('Invoice 1');
  await b.section('Basics');
  await page.getByLabel('Title').fill('Builder real-stack e2e v2');
  await b.expectValid();
  await page.getByRole('button', { name: 'Publish version 2' }).click();
  await expect(page.getByTestId('published-notice')).toContainText('Published version 2');
});
