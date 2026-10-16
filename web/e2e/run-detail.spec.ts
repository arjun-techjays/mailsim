import { expect, test } from '@playwright/test';
import { expectAccessible, expectEmailFrameInert, KEYS, XSS_HTML } from './fixtures';
import { ConnectPage } from './pages/ConnectPage';
import { RunPage } from './pages/RunPage';

test.describe('run detail navigation', () => {
  test.beforeEach(async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
  });

  test('mailbox browser: folders, pagination, thread and safe message view', async ({ page }) => {
    const run = new RunPage(page);
    await run.goto('run_demo');
    await expect(run.summary()).toContainText('RUNNING');
    await expectAccessible(page, 'run overview');
    await run.tab('Mailboxes');

    const list = run.messageList();
    await expect(list.locator('tbody tr')).toHaveCount(20);
    await page.getByRole('button', { name: 'Load more messages' }).click();
    await expect(list.locator('tbody tr')).toHaveCount(25);
    await expect(page.getByRole('button', { name: 'Load more messages' })).toHaveCount(0);

    await run.folder('Asha Rao (Finance Manager)', 'sent');
    await expect(list.locator('tbody tr')).toHaveCount(1);
    await run.mailbox('Asha Rao (Finance Manager)');

    await run.openMessage('Re: Invoice 4471 - updated bank details', 1); // the vendor's HTML mail (newest first: our reply is 0)
    const view = page.getByTestId('message-view');
    await expect(view).toContainText('Supplier Billing <billing@supplier.test>');
    await expect(view.getByTestId('email-text')).toContainText('new bank account');
    await expect(view.getByTestId('thread').getByRole('listitem')).toHaveCount(3);
    await expectAccessible(page, 'mailbox browser');

    // HTML is only rendered in an empty-sandbox iframe; its script/onerror never execute.
    await view.getByRole('button', { name: 'Show HTML (sandboxed)' }).click();
    await expect(page.frameLocator('[data-testid="email-html-frame"]').getByText('new bank account')).toBeVisible();
    await expectEmailFrameInert(page);
    expect(await page.locator('script', { hasText: '__mailsimPwned' }).count()).toBe(0);

    // thread navigation
    await view.getByTestId('thread').getByRole('button', { name: 'Open Invoice 4471' }).click();
    await expect(view.getByRole('heading', { level: 3 })).toHaveText('Invoice 4471');
    await expect(view.getByTestId('attachments')).toContainText('invoice-4471.pdf');
    await expect(page).toHaveURL(/message=msg_invoice_1/);

    // raw MIME link jumps to the artifact reference
    await view.getByRole('button', { name: 'Show headers' }).click();
    await view.getByTestId('raw-mime-link').click();
    await expect(page.getByTestId('highlighted-artifact')).toContainText('blob/raw/msg_invoice_1.eml');
    // artifacts download as files (ArtifactOut.url); the trace has none
    const [file] = await Promise.all([
      page.waitForEvent('download'),
      page.getByTestId('highlighted-artifact').getByRole('button', { name: 'Download blob/raw/msg_invoice_1.eml' }).click(),
    ]);
    expect(file.suggestedFilename()).toBe('msg_invoice_1.eml');
    await expect(page.getByTestId('artifacts-table').getByRole('row', { name: /trace\// })).not.toContainText('Download');

    // other mailbox
    await run.tab('Mailboxes');
    await run.mailbox('Supplier Billing (Vendor)');
    await expect(run.messageList().locator('tbody tr')).toHaveCount(1);
  });

  test('event trace: cursor pagination and filters', async ({ page }) => {
    const run = new RunPage(page);
    await run.goto('run_demo');
    await run.tab('Event trace');
    const rows = page.getByTestId('event-table').locator('tbody tr');
    await expect(rows).toHaveCount(50);
    await page.getByRole('button', { name: 'Load more events' }).click();
    await expect(rows).toHaveCount(100);
    await page.getByRole('button', { name: 'Load more events' }).click();
    await expect(rows).toHaveCount(120);
    await expect(page.getByRole('button', { name: 'Load more events' })).toHaveCount(0);

    await page.getByLabel('Actor').selectOption('controller');
    await expect(rows).toHaveCount(24);
    await page.getByLabel('Epoch').selectOption('2');
    await expect(rows).toHaveCount(4);
    await page.getByLabel('Actor').selectOption('');
    await page.getByLabel('Type').selectOption('message.sent');
    await expect(rows).toHaveCount(4);
    await expectAccessible(page, 'event trace');

    // UI-7: another client's rejected start is recorded without events: "none", never "#121–#120".
    const status = await page.evaluate(async () => {
      const res = await fetch('/v1/runs/run_demo/start', { method: 'POST', headers: { Authorization: 'Bearer mk_controller' } });
      return res.status;
    });
    expect(status).toBe(409);
    await run.tab('Actions');
    const actions = page.getByTestId('actions-table');
    await expect(actions.locator('tbody tr')).toHaveCount(5);
    await expect(actions.getByRole('row', { name: /CONFLICT/ })).toContainText('none');
    await expect(actions).not.toContainText('#121');
    await expect(actions.getByRole('row', { name: /reply/ })).toContainText('#3–#4');
  });

  test('empty trace for a run without events', async ({ page }) => {
    const run = new RunPage(page);
    await run.goto('run_empty');
    await run.tab('Event trace');
    await expect(page.getByTestId('empty-state')).toContainText('This run has no events yet.');
    await expectAccessible(page, 'empty trace');
  });

  test('XSS canary: the payload WOULD set the flag in an unsandboxed frame (so the checks can fail)', async ({ page }) => {
    await new RunPage(page).goto('run_demo');
    await page.evaluate((html) => {
      const f = document.createElement('iframe');
      f.srcdoc = html; // deliberately unsandboxed, test-only
      document.body.append(f);
    }, XSS_HTML);
    await expect
      .poll(() => page.evaluate(() => (window as unknown as { __mailsimPwned?: unknown }).__mailsimPwned))
      .not.toBeUndefined();
  });
});
