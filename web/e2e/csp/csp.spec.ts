import { expect, test, type Page } from '@playwright/test';
import { expectAccessible, KEYS } from '../fixtures';
import { ConnectPage } from '../pages/ConnectPage';
import { EvaluationPage } from '../pages/EvaluationPage';
import { RunPage } from '../pages/RunPage';

/** Collects CSP violations reported by the dashboard document (not the sandboxed email frame). */
function watchCsp(page: Page): string[] {
  const violations: string[] = [];
  page.on('console', (msg) => {
    const text = msg.text();
    const fromEmailFrame = msg.location().url === 'about:srcdoc';
    if (/Content Security Policy|Refused to/i.test(text) && !fromEmailFrame) violations.push(text);
  });
  page.on('pageerror', (err) => violations.push(`pageerror: ${err.message}`));
  return violations;
}

test('production build works under the strict CSP and security headers', async ({ page }) => {
  const violations = watchCsp(page);
  const res = await page.goto('/connect');
  const headers = res?.headers() ?? {};
  expect(headers['content-security-policy']).toContain("frame-ancestors 'none'");
  expect(headers['content-security-policy']).toContain("script-src 'self'");
  expect(headers['x-content-type-options']).toBe('nosniff');
  expect(headers['referrer-policy']).toBe('no-referrer');
  expect(await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute('content')).toContain(
    "object-src 'none'",
  );

  await new ConnectPage(page).connectAs(KEYS.evaluator, 'evaluator');
  const run = new RunPage(page);
  await run.goto('run_demo');
  await run.tab('Mailboxes');
  await run.openMessage('Re: Invoice 4471 - updated bank details', 1); // the vendor's HTML mail (newest first: our reply is 0)
  await page.getByRole('button', { name: 'Show HTML (sandboxed)' }).click();
  const frame = page.frameLocator('[data-testid="email-html-frame"]');
  await expect(frame.getByText('new bank account')).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { __mailsimPwned?: unknown }).__mailsimPwned)).toBeUndefined();
  await run.tab('Event trace');
  await expect(page.getByTestId('event-table')).toBeVisible();

  const ev = new EvaluationPage(page);
  await ev.goto('run_demo');
  await expect(ev.report()).toContainText('FAILED');
  await page.goto('/scenarios/finance.invoice-bank-change/compare');
  await expect(page.getByRole('img', { name: /Mean accuracy by build/ })).toBeVisible();
  await expectAccessible(page, 'compare (csp build)');

  expect(violations).toEqual([]);
});

test('source maps are not shipped', async ({ page, request }) => {
  await page.goto('/connect');
  const src = await page.locator('script[type="module"]').first().getAttribute('src');
  const js = await (await request.get(src as string)).text();
  expect(js).not.toContain('sourceMappingURL');
});

test('the policy is enforced (inline script blocked) and the violation watcher sees it', async ({ page }) => {
  const violations = watchCsp(page);
  await page.goto('/connect');
  await page.addScriptTag({ content: 'window.__inlineRan = true;' }).catch(() => undefined);
  expect(await page.evaluate(() => (window as unknown as { __inlineRan?: boolean }).__inlineRan)).toBeUndefined();
  await expect.poll(() => violations.length).toBeGreaterThan(0);
});

// ---- connect & guide (UX-F3)
test('home, connect-your-agent and dark mode work under the strict CSP without inline styles', async ({ page }) => {
  const violations = watchCsp(page);
  await page.emulateMedia({ colorScheme: 'dark' });
  await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
  await page.goto('/');
  await expect(page.getByTestId('step-4-state')).toHaveText('Done');
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe('rgb(18, 20, 23)');
  expect(await page.locator('[style]').count()).toBe(0);

  await expect(page.getByTestId('step-4').getByRole('link', { name: 'Connect your agent' })).toHaveAttribute('href', '/runs/run_empty/connect');
  await page.goto('/runs/run_demo/connect'); // the RUNNING demo run (mk_agent belongs to it)
  await page.getByLabel('Saved agent key').fill(KEYS.agent);
  await page.getByRole('button', { name: 'Use this key' }).click();
  await page.getByRole('radio', { name: /Microsoft Graph/ }).check();
  await page.getByRole('button', { name: 'Test Microsoft Graph connection' }).click();
  await expect(page.getByTestId('test-ok-graph')).toBeVisible();
  await page.getByRole('button', { name: 'Mint a new agent key' }).click();
  await expect(page.getByRole('alertdialog')).toBeVisible();
  expect(await page.locator('[style]').count()).toBe(0);
  await page.getByLabel('Theme').selectOption('light');
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe('rgb(255, 255, 255)');
  await expectAccessible(page, 'connect (csp build)');
  expect(violations).toEqual([]);
});

test('answer-key editor, mailbox browser and activity work under the strict CSP', async ({ page }) => {
  const violations = watchCsp(page);
  await new ConnectPage(page).connectAs(KEYS.evaluator, 'evaluator');
  await page.goto('/scenarios/finance.invoice-bank-change/answer-key?from=2');
  await expect(page.locator('.ak-card')).toHaveCount(3);
  await expect(page.getByTestId('validation-status')).toContainText('No problems found');
  await page.getByRole('button', { name: 'YAML' }).click();
  await expect(page.getByLabel('Answer key as YAML')).toHaveValue(/answer_key:/);
  const run = new RunPage(page);
  await run.goto('run_demo');
  await run.tab('Mailboxes');
  await run.openMessage('Invoice 4471');
  await run.tab('Activity');
  await expect(page.getByTestId('activity-table')).toBeVisible();
  expect(violations).toEqual([]);
});
