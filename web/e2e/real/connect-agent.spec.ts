import { expect, test, type APIRequestContext } from '@playwright/test';
import { expectAccessible } from '../fixtures';
import { ConnectPage } from '../pages/ConnectPage';

// Real-stack "Connect your agent" (UX-F3). Needs the UX-B implementation of
//   GET /v1/system/status, GET /v1/runs/{id}/connection, POST /v1/runs/{id}/connection-tests
// and skips (saying so) while those answer 501. Run with `npm run e2e:real` and
//   MAILSIM_API_URL, MAILSIM_CONTROLLER_KEY, optional MAILSIM_SCENARIO_KEY
// IMAP/SMTP tests need `python -m mailsim.serve protocols` running against the same database.
// Creates one run through the UI and mints/revokes that run's agent keys only.
const API = process.env.MAILSIM_API_URL;
const CONTROLLER = process.env.MAILSIM_CONTROLLER_KEY;
const SCENARIO = process.env.MAILSIM_SCENARIO_KEY ?? 'finance.invoice-bank-change';

test.skip(!API || !CONTROLLER, 'MAILSIM_API_URL / MAILSIM_CONTROLLER_KEY not set; real-stack connect e2e skipped');
test.describe.configure({ mode: 'serial' });

interface Status {
  listeners: Record<string, { running: boolean }>;
  interfaces: string[];
}
let status: Status | null = null;

async function systemStatus(request: APIRequestContext): Promise<Status | null> {
  const res = await request.get('/v1/system/status', { headers: { Authorization: `Bearer ${CONTROLLER}` } });
  return res.ok() ? ((await res.json()) as Status) : null;
}

test.beforeAll(async ({ request }) => {
  status = await systemStatus(request);
});

test('connect a real run through every served interface', async ({ page, request }) => {
  test.skip(!status, 'GET /v1/system/status is not implemented yet (UX-B not landed)');
  await new ConnectPage(page).connectAs(CONTROLLER as string, 'controller');
  await expect(page.getByTestId('status-pill')).toContainText('LLM:');
  await page.goto(`/scenarios/${encodeURIComponent(SCENARIO)}/start`);
  await page.getByRole('button', { name: 'Create run' }).click();
  const key = await page.getByTestId('agent-api-key').inputValue();
  expect(key).toMatch(/^msk_[0-9a-f]{12}_/);
  await page.getByRole('link', { name: 'Next: connect your agent' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Connect your agent' })).toBeVisible();
  await expect(page.getByTestId('agent-key-value')).toContainText('••••••••');

  const served = new Set(status?.interfaces ?? []);
  const listenerUp = (n: string) => status?.listeners?.[n]?.running === true;
  const cases: [RegExp, string, string, boolean][] = [
    [/Gmail API/, 'Test Gmail API connection', 'gmail', served.has('gmail')],
    [/Microsoft Graph/, 'Test Microsoft Graph connection', 'graph', served.has('graph')],
    [/MCP over HTTP/, 'Test MCP over HTTP connection', 'mcp_http', served.has('mcp_http')],
    [/MCP over stdio/, 'Test MCP over stdio connection', 'mcp_stdio', served.has('mcp_stdio')],
    [/Neutral REST/, 'Test REST connection', 'rest', true],
    [/IMAP \/ SMTP/, 'Test IMAP connection', 'imap', listenerUp('imap')],
    [/IMAP \/ SMTP/, 'Test SMTP connection', 'smtp', listenerUp('smtp')],
  ];
  for (const [radio, button, id, expectOk] of cases) {
    await page.getByRole('radio', { name: radio }).check();
    await page.getByRole('button', { name: button }).click();
    await expect(page.getByTestId(new RegExp(`^test-(ok|fail)-${id}$`))).toBeVisible({ timeout: 30_000 });
    if (expectOk) await expect(page.getByTestId(`test-ok-${id}`), `${id} connection test`).toBeVisible();
  }
  await expectAccessible(page, 'connect (real)');

  // the dev proxy forwards the agent interfaces, so a dashboard-origin client reaches them
  const gmail = await request.get('/gmail/v1/users/me/profile', { headers: { Authorization: `Bearer ${key}` } });
  expect(gmail.status()).toBe(200);
  const graph = await request.get('/graph/v1.0/me', { headers: { Authorization: `Bearer ${key}` } });
  expect(graph.status()).toBe(200);

  // a wrong key fails with the exact error
  await page.getByRole('button', { name: 'Forget key' }).click();
  await page.getByLabel('Saved agent key').fill('msk_000000000000_notarealkeynotarealkeynotarealkey1');
  await page.getByRole('button', { name: 'Use this key' }).click();
  await page.getByRole('radio', { name: /Neutral REST/ }).check();
  await page.getByRole('button', { name: 'Test REST connection' }).click();
  await expect(page.getByTestId('test-fail-rest')).toContainText('failed');

  // mint (revokes the original key), then revoke the new one
  await page.getByRole('button', { name: 'Mint a new agent key' }).click();
  await page.getByRole('button', { name: 'Mint key' }).click();
  await expect(page.getByText(/New agent key minted/)).toBeVisible();
  const old = await request.get('/gmail/v1/users/me/profile', { headers: { Authorization: `Bearer ${key}` } });
  expect(old.status()).toBe(401);
  await page.getByRole('button', { name: 'Test REST connection' }).click();
  await expect(page.getByTestId('test-ok-rest')).toBeVisible();
  const active = page.getByTestId('agent-keys').getByRole('row').filter({ hasText: 'active' });
  await expect(active).toHaveCount(1);
  await active.getByRole('button', { name: /^Revoke/ }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Revoke' }).click();
  await expect(page.getByTestId('no-key')).toBeVisible();
});
