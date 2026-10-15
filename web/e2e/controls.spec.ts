import { expect, test } from '@playwright/test';
import { KEYS } from './fixtures';
import { ConnectPage } from './pages/ConnectPage';
import { RunPage } from './pages/RunPage';

test.describe('run controls', () => {
  test.beforeEach(async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
  });

  test('inject and advance update the run', async ({ page }) => {
    const run = new RunPage(page);
    await run.goto('run_demo');

    await run.inject({ from: 'ceo@acme.test', subject: 'Urgent wire', text: 'Pay now', html: '<script>window.__mailsimPwned=1</script><b>Pay</b>' });
    await expect(page.getByTestId('inject-result')).toContainText('Delivered to simulated mailbox');

    await run.advance(600);
    await expect(run.controlResult()).toContainText('fired evt_vendor_followup');
    await expect(page.getByTestId('run-virtual-time')).toHaveText('2026-03-02T09:12:00Z');

    await page.getByRole('button', { name: 'Scheduler tick' }).click();
    await expect(run.controlResult()).toContainText('no scheduled events fired');

    await run.tab('Mailboxes');
    await page.getByRole('button', { name: 'Load more messages' }).click();
    await expect(run.messageList()).toContainText('Urgent wire');
    await expect(run.messageList()).toContainText('Following up: bank details');
    await run.openMessage('Urgent wire');
    await page.getByRole('button', { name: 'Show HTML (sandboxed)' }).click();
    await expect(page.getByTestId('email-html-frame')).toHaveAttribute('sandbox', '');
    expect(await page.evaluate(() => (window as unknown as { __mailsimPwned?: unknown }).__mailsimPwned)).toBeUndefined();

    await run.tab('Event trace');
    await page.getByLabel('Type').selectOption('clock.advanced');
    await expect(page.getByTestId('event-table')).toContainText('clock.advance');
  });

  test('reset, replay and cancel', async ({ page }) => {
    const run = new RunPage(page);
    await run.goto('run_demo');
    await expect(page.getByTestId('run-epoch')).toHaveText('2');
    await page.getByRole('button', { name: 'Reset (new epoch)' }).click();
    await expect(run.controlResult()).toContainText('Reset: run is now RUNNING (epoch 3)');
    await expect(page.getByTestId('run-epoch')).toHaveText('3');

    await page.getByRole('button', { name: 'Replay & compare hash' }).click();
    const replay = page.getByTestId('replay-result');
    await expect(replay).toContainText('Replay matches');
    await expect(replay).toContainText('identical');

    await page.getByRole('button', { name: 'Start' }).click();
    await expect(page.getByTestId('api-error')).toContainText('CONFLICT (HTTP 409)');

    await page.getByRole('button', { name: 'Cancel run' }).click();
    await expect(run.controlResult()).toContainText('CANCELLED');
    await run.advance(5);
    await expect(page.getByTestId('api-error')).toContainText('RUN_NOT_ACTIVE (HTTP 409)');
    await expect(page.getByTestId('api-error')).toContainText('This operation needs a RUNNING run.');
  });

  test('inject form validation: client-side and server envelope', async ({ page }) => {
    const run = new RunPage(page);
    await run.goto('run_demo');
    await page.getByRole('form', { name: 'Inject message' }).getByLabel('To (comma-separated)').fill('');
    await run.inject({});
    const summary = page.getByTestId('inject-validation');
    await expect(summary).toContainText('Fix 3 field(s)');
    await expect(page.getByLabel('From', { exact: true })).toHaveAttribute('aria-invalid', 'true');
    await summary.getByRole('link', { name: 'To (comma-separated)' }).click();
    await expect(page.getByLabel('To (comma-separated)')).toBeFocused();

    await run.inject({ from: 'not-an-email', to: 'finance@acme.test', text: 'hi' });
    await expect(page.getByTestId('inject-validation')).toHaveCount(0);
    const err = page.getByTestId('api-error');
    await expect(err).toContainText('INVALID_REQUEST (HTTP 400)');
    await expect(err.getByRole('list', { name: 'Field errors' })).toContainText('message.from: value is not a valid email address');
    await expect(err.getByTestId('request-id')).toHaveText(/^req_mock_/);

    await run.advance(-1);
    await expect(page.getByText('Enter a number of seconds (0 or more).')).toBeVisible();
  });
});
