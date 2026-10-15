import AxeBuilder from '@axe-core/playwright';
import { expect, type Page } from '@playwright/test';

export const KEYS = {
  controller: 'mk_controller',
  controllerEmpty: 'mk_controller_empty',
  evaluator: 'mk_evaluator',
  agent: 'mk_agent',
  revocable: 'mk_controller_revocable',
} as const;

/** Fails on serious/critical axe violations (WCAG 2.x A/AA rules). */
export async function expectAccessible(page: Page, label: string): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
  const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  const summary = serious.map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
  expect(serious, `${label}: ${summary.join('\n')}`).toEqual([]);
}

/** Payload that would set the flag on the dashboard window if any email markup ever executed. */
export const XSS_HTML =
  '<meta http-equiv="refresh" content="0;url=https://evil.example/">' +
  '<script>parent.__mailsimPwned = "script"; top.__mailsimPwned = "script";</script>' +
  '<img src="x" alt="" onerror="parent.__mailsimPwned = \'onerror\'; top.__mailsimPwned = \'onerror\'">' +
  '<a href="https://evil.example/pay" target="_top">Pay here</a><b>Pay</b>';

/** Asserts the email HTML frame is an empty sandbox, sanitized, and nothing reached the dashboard. */
export async function expectEmailFrameInert(page: Page): Promise<void> {
  const frameEl = page.getByTestId('email-html-frame');
  await expect(frameEl).toHaveAttribute('sandbox', '');
  const frame = page.frameLocator('[data-testid="email-html-frame"]');
  await expect(frame.locator('body')).toBeVisible(); // frame document loaded (handlers would have fired)
  await expect(frame.locator('meta[http-equiv="refresh"], script, [onerror], [target]')).toHaveCount(0);
  const flags = await page.evaluate(() => {
    const w = window as unknown as { __mailsimPwned?: unknown };
    const t = window.top as unknown as { __mailsimPwned?: unknown };
    return [w.__mailsimPwned, t.__mailsimPwned];
  });
  expect(flags).toEqual([undefined, undefined]);
}
