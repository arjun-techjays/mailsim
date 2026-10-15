import { expect, test, type Page } from '@playwright/test';
import { expectAccessible, KEYS } from '../fixtures';
import { BuilderPage } from '../pages/BuilderPage';
import { ConnectPage } from '../pages/ConnectPage';

/** CSP violations reported by the dashboard document (not the sandboxed email preview). */
function watchCsp(page: Page): string[] {
  const violations: string[] = [];
  page.on('console', (msg) => {
    const fromEmailFrame = msg.location().url === 'about:srcdoc';
    if (/Content Security Policy|Refused to/i.test(msg.text()) && !fromEmailFrame) violations.push(msg.text());
  });
  page.on('pageerror', (err) => violations.push(`pageerror: ${err.message}`));
  return violations;
}

test('the scenario builder works under the strict CSP (styles, YAML view, sandboxed HTML preview)', async ({ page }) => {
  const violations = watchCsp(page);
  await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
  await page.goto('/scenarios/new');
  await page.getByRole('button', { name: /Finance example/ }).click();
  const b = new BuilderPage(page);
  await b.expectValid();
  // builder.css is applied (bundled stylesheet, no inline styles)
  await expect(page.locator('.b-layout')).toHaveCSS('display', 'grid');
  await page.getByRole('button', { name: 'Show YAML' }).click();
  await b.yamlContains('id: finance.invoice-review');
  await b.section('Initial messages');
  await page.getByRole('button', { name: /Compose a message for Finance Manager/ }).click();
  await page.getByLabel('HTML body (optional)').fill('<p>Hello <b>there</b></p><img src=x onerror="parent.__mailsimPwned=1">');
  await page.getByRole('button', { name: 'Preview HTML' }).click();
  await expect(page.frameLocator('[data-testid="email-html-frame"]').getByText('Hello')).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { __mailsimPwned?: unknown }).__mailsimPwned)).toBeUndefined();
  await page.getByRole('button', { name: 'Hide HTML preview' }).click(); // axe cannot enter the empty sandbox
  await expectAccessible(page, 'builder (csp build)');
  expect(violations).toEqual([]);
});
