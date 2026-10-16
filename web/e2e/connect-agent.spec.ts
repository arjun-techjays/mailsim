import { expect, test, type Page } from '@playwright/test';
import { expectAccessible, KEYS } from './fixtures';
import { ConnectPage } from './pages/ConnectPage';

// "Connect your agent" (/runs/:runId/connect) against the mock API. The page keeps agent keys
// in memory only, so each test pastes or mints a key after the last full page load.

const RUN = '/runs/run_demo/connect';

async function open(page: Page, query = '') {
  await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
  await page.goto(`${RUN}${query}`);
  await expect(page.getByTestId('connection-summary')).toContainText('finance@acme.test');
}

async function useKey(page: Page, key: string) {
  await page.getByLabel('Saved agent key').fill(key);
  await page.getByRole('button', { name: 'Use this key' }).click();
  await expect(page.getByTestId('agent-key-value')).toBeVisible();
}

async function choose(page: Page, name: RegExp) {
  await page.getByRole('radio', { name }).check();
}

const INTERFACES: { radio: RegExp; heading: RegExp; values: string[]; snippets: string[]; tests: [string, string][] }[] = [
  {
    radio: /Gmail API/,
    heading: /Gmail API/,
    values: ['http://127.0.0.1:8000/gmail/v1', 'OAuth access token'],
    snippets: ['client_options={"api_endpoint": "http://127.0.0.1:8000/"}', "'http://127.0.0.1:8000/gmail/v1/users/me/messages?q=is:unread'"],
    tests: [['Test Gmail API connection', 'gmail']],
  },
  {
    radio: /Microsoft Graph/,
    heading: /Microsoft Graph/,
    values: ['http://127.0.0.1:8000/graph/v1.0', 'bearer access token'],
    snippets: ['BaseBearerTokenAuthenticationProvider(SandboxKey(AGENT_KEY))', 'AllowedHostsValidator(["127.0.0.1:8000"])'],
    tests: [['Test Microsoft Graph connection', 'graph']],
  },
  {
    radio: /IMAP \/ SMTP/,
    heading: /IMAP \/ SMTP/,
    values: ['127.0.0.1:1143', '127.0.0.1:1587', 'mailbox address as the username'],
    snippets: ['imaplib.IMAP4("127.0.0.1", 1143)', 'smtplib.SMTP("127.0.0.1", 1587)'],
    tests: [
      ['Test IMAP connection', 'imap'],
      ['Test SMTP connection', 'smtp'],
    ],
  },
  {
    radio: /MCP over HTTP/,
    heading: /MCP over HTTP/,
    values: ['http://127.0.0.1:8000/mcp'],
    snippets: ["claude mcp add --transport http mailsim 'http://127.0.0.1:8000/mcp'", '"type": "http"'],
    tests: [['Test MCP over HTTP connection', 'mcp_http']],
  },
  {
    radio: /MCP over stdio/,
    heading: /MCP over stdio/,
    values: ['uv run python -m mailsim.serve mcp-stdio', 'MAILSIM_API_KEY'],
    snippets: ['claude mcp add mailsim --env MAILSIM_API_KEY=', '"command": "uv"'],
    tests: [['Test MCP over stdio connection', 'mcp_stdio']],
  },
  {
    radio: /Neutral REST/,
    heading: /Neutral REST/,
    values: ['http://127.0.0.1:8000/v1'],
    snippets: ["'http://127.0.0.1:8000/v1/mailboxes/mbx_finance/folders'"],
    tests: [['Test REST connection', 'rest']],
  },
];

test.describe('connect your agent', () => {
  test('each interface shows the right values and snippets, and its connection test passes', async ({ page }) => {
    await open(page);
    await useKey(page, KEYS.agent);
    for (const iface of INTERFACES) {
      await choose(page, iface.radio);
      const details = page.locator('section', { has: page.getByRole('heading', { level: 2, name: iface.heading }) });
      for (const v of iface.values) await expect(details).toContainText(v);
      for (const s of iface.snippets) await expect(details.locator('pre')).toContainText([s]);
      await expect(details.locator('pre').first()).toContainText('<AGENT_KEY>');
      for (const [button, id] of iface.tests) {
        await details.getByRole('button', { name: button }).click();
        await expect(page.getByTestId(`test-ok-${id}`)).toBeVisible();
      }
    }
    await expect(page).toHaveURL(/\?interface=rest$/);
    expect(page.url()).not.toContain('msk_');
    expect(page.url()).not.toContain(KEYS.agent);
  });

  test('revealing the key fills the snippets; copy works by keyboard', async ({ page }) => {
    await open(page, '?interface=mcp_http');
    await useKey(page, KEYS.agent);
    await page.getByRole('button', { name: 'Reveal key' }).click();
    await expect(page.getByTestId('snippet-mcp-http-claude')).toContainText(`Bearer ${KEYS.agent}`);
    const copy = page.getByTestId('snippet-mcp-http-claude').getByRole('button', { name: 'Copy Claude Code' });
    await copy.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('snippet-mcp-http-claude')).toContainText('Copied to clipboard.');
    expect(await page.evaluate(() => navigator.clipboard.readText())).toContain(`Bearer ${KEYS.agent}`);
    const stored = await page.evaluate(() => JSON.stringify({ ...window.localStorage, ...window.sessionStorage }));
    expect(stored).not.toContain(KEYS.agent);
  });

  test('a wrong key and a missing key give actionable errors', async ({ page }) => {
    await open(page, '?interface=gmail');
    await page.getByRole('button', { name: 'Test Gmail API connection' }).click();
    await expect(page.getByText(/Testing needs the agent key/)).toBeVisible();
    await useKey(page, 'msk_000000000000_notTheRightKey');
    await page.getByRole('button', { name: 'Test Gmail API connection' }).click();
    const fail = page.getByTestId('test-fail-gmail');
    await expect(fail).toContainText('Gmail API: failed (UNAUTHENTICATED, 401)');
    await expect(fail).toContainText('Request had invalid authentication credentials.');
    await expect(fail).toContainText('Mint a new agent key');
  });

  test('mint a new key (confirmed), then revoke it (confirmed)', async ({ page }) => {
    await open(page);
    await expect(page.getByTestId('no-key')).toBeVisible();
    await page.getByRole('button', { name: 'Mint a new agent key' }).click();
    const dialog = page.getByRole('alertdialog', { name: 'Mint a new agent key?' });
    await expect(dialog).toContainText('key_mk_agent');
    await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await page.getByRole('button', { name: 'Mint a new agent key' }).click();
    await page.getByRole('button', { name: 'Mint key' }).click();
    await expect(page.getByText(/New agent key minted/)).toBeVisible();
    await expect(page.getByTestId('agent-key-value')).toContainText('msk_000000000001_mock');
    const keys = page.getByTestId('agent-keys');
    // Minting no longer revokes: the old key stays active until revoked explicitly.
    await expect(keys.getByRole('row', { name: /key_minted_0001/ })).toContainText('active');
    await expect(keys.getByRole('row', { name: /key_mk_agent/ })).toContainText('active');
    const notice = page.getByTestId('minted-notice');
    await expect(notice).toContainText('other agent key still works');
    await notice.getByRole('button', { name: 'Revoke the other key' }).click();
    await page.getByRole('alertdialog', { name: 'Revoke key_mk_agent?' }).getByRole('button', { name: 'Revoke' }).click();
    await expect(keys.getByRole('row', { name: /key_mk_agent/ })).toContainText('revoked');
    await expect(keys.getByRole('row', { name: /key_minted_0001/ })).toContainText('active');

    await choose(page, /Neutral REST/);
    await page.getByRole('button', { name: 'Test REST connection' }).click();
    await expect(page.getByTestId('test-ok-rest')).toBeVisible();

    await keys.getByRole('button', { name: 'Revoke key_minted_0001' }).click();
    await page.getByRole('alertdialog', { name: 'Revoke key_minted_0001?' }).getByRole('button', { name: 'Revoke' }).click();
    await expect(keys.getByRole('row', { name: /key_minted_0001/ })).toContainText('revoked');
    await expect(page.getByTestId('no-key')).toBeVisible();
  });

  test('an operator key is refused as PERMISSION_DENIED; keys are dropped on Disconnect', async ({ page }) => {
    await open(page, '?interface=rest');
    await useKey(page, KEYS.controller);
    await page.getByRole('button', { name: 'Test REST connection' }).click();
    await expect(page.getByTestId('test-fail-rest')).toContainText('REST: failed (PERMISSION_DENIED)');
    await expect(page.getByTestId('test-fail-rest')).toContainText('controller or evaluator key');

    await page.getByRole('button', { name: 'Mint a new agent key' }).click();
    await page.getByRole('button', { name: 'Mint key' }).click();
    await expect(page.getByTestId('minted-notice')).toBeFocused();
    await expect(page.getByTestId('held-marker')).toBeVisible();
    // client-side: Disconnect, reconnect, come back without a reload
    await page.getByRole('button', { name: 'Disconnect' }).click();
    await page.getByLabel('API key').fill(KEYS.controller);
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Runs' }).click();
    await page.getByTestId('runs-table').getByRole('link', { name: 'run_demo' }).click();
    await page.getByRole('link', { name: 'Connect your agent' }).click();
    await expect(page.getByTestId('agent-keys').getByRole('row', { name: /key_minted_0001/ })).toContainText('active');
    await expect(page.getByTestId('no-key')).toBeVisible(); // the minted key is no longer held
    await expect(page.getByTestId('held-marker')).toHaveCount(0);
  });

  test('start a run, then "Next: connect your agent" carries the key in memory', async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
    await page.goto('/scenarios/finance.invoice-bank-change/start');
    await page.getByRole('button', { name: 'Create run' }).click();
    await page.getByRole('link', { name: 'Next: connect your agent' }).click();
    await expect(page).toHaveURL(/\/runs\/run_001\/connect$/);
    await expect(page.getByTestId('agent-key-value')).toContainText('••••••••');
    await choose(page, /Gmail API/);
    await page.getByRole('button', { name: 'Test Gmail API connection' }).click();
    await expect(page.getByTestId('test-ok-gmail')).toBeVisible();
    // the key never reaches the URL, history state or storage
    expect(await page.evaluate(() => JSON.stringify(history.state))).not.toContain('mk_agent_run_001');
    expect(await page.evaluate(() => JSON.stringify({ ...window.localStorage, ...window.sessionStorage }))).not.toContain('mk_agent_run_001');
  });

  test('run detail offers "Connect your agent"', async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
    await page.goto('/runs/run_demo');
    await page.getByRole('link', { name: 'Connect your agent' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Connect your agent' })).toBeVisible();
  });

  for (const scheme of ['light', 'dark'] as const) {
    test(`connect page is accessible (${scheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await open(page, '?interface=imap_smtp');
      await useKey(page, 'msk_000000000000_wrong');
      await page.getByRole('button', { name: 'Test IMAP connection' }).click();
      await expect(page.getByTestId('test-fail-imap')).toBeVisible();
      await page.getByRole('button', { name: 'Mint a new agent key' }).click();
      await expectAccessible(page, `connect ${scheme}`);
    });
  }
});
