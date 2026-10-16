import { expect, test } from '@playwright/test';
import { expectAccessible, expectEmailFrameInert, KEYS, XSS_HTML } from './fixtures';
import { ConnectPage } from './pages/ConnectPage';
import { RunPage } from './pages/RunPage';

test.describe('run controls', () => {
  test.beforeEach(async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
  });

  test('inject and advance update the run', async ({ page }) => {
    const run = new RunPage(page);
    await run.goto('run_demo');

    await run.inject({ from: 'ceo@acme.test', subject: 'Urgent wire', text: 'Pay now', html: XSS_HTML });
    await expect(page.getByTestId('inject-result')).toContainText('Delivered to simulated mailbox');

    await run.advance(600);
    await expect(run.controlResult()).toContainText('fired evt_vendor_followup');
    await expect(run.controlResult()).toContainText('1 persona reply delivered');
    await expect(page.getByTestId('run-virtual-time')).toHaveText('2026-03-02T09:12:00Z');

    await page.getByRole('button', { name: 'Scheduler tick' }).click();
    await expect(run.controlResult()).toContainText('no scheduled events fired');

    await run.tab('Mailboxes');
    await page.getByRole('button', { name: 'Load more messages' }).click();
    await expect(run.messageList()).toContainText('Urgent wire');
    await expect(run.messageList()).toContainText('Following up: bank details');
    await run.openMessage('Urgent wire');
    await page.getByRole('button', { name: 'Show HTML (sandboxed)' }).click();
    await expectEmailFrameInert(page);
    await expect(page.frameLocator('[data-testid="email-html-frame"]').getByText('[https://evil.example/pay]')).toBeVisible();
    await expect(page).toHaveURL(/\/runs\/run_demo/);

    await run.tab('Event trace');
    await page.getByLabel('Type').selectOption('clock.advanced');
    await expect(page.getByTestId('event-table')).toContainText('clock.advance');
  });

  test('reset and cancel ask first; actions follow the run state; replays are marked (UI-6)', async ({ page }) => {
    const run = new RunPage(page);
    await run.goto('run_demo');
    await expect(page.getByTestId('run-epoch')).toHaveText('2');
    await expect(page.getByTestId('controls-state')).toContainText('This run is RUNNING');
    await expect(page.getByRole('button', { name: 'Start' })).toBeDisabled();

    await page.getByRole('button', { name: 'Reset (new epoch)' }).click();
    const reset = page.getByRole('alertdialog', { name: 'Reset this run to its starting state?' });
    await expect(reset.getByRole('button', { name: 'Cancel' })).toBeFocused();
    await expectAccessible(page, 'reset confirmation');
    await page.keyboard.press('Escape');
    await expect(reset).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Reset (new epoch)' })).toBeFocused();
    await expect(page.getByTestId('run-epoch')).toHaveText('2');
    await page.getByRole('button', { name: 'Reset (new epoch)' }).click();
    await page.getByRole('button', { name: 'Reset run' }).click();
    await expect(run.controlResult()).toContainText('Reset: run is now RUNNING (epoch 3)');
    await expect(page.getByTestId('run-epoch')).toHaveText('3');

    await page.getByRole('button', { name: 'Replay & compare hash' }).click();
    const replay = page.getByTestId('replay-result');
    await expect(replay).toContainText('Replay matches');
    await expect(replay).toContainText('identical');

    await page.getByRole('button', { name: 'Cancel run' }).click();
    await page.getByRole('alertdialog', { name: 'Cancel this run?' }).getByRole('button', { name: 'Cancel run' }).click();
    await expect(run.controlResult()).toContainText('CANCELLED');
    await expect(page.getByTestId('controls-state')).toContainText('This run is CANCELLED and cannot change any more');
    for (const name of ['Start', 'Cancel run', 'Reset (new epoch)', 'Scheduler tick', 'Advance clock']) {
      await expect(page.getByRole('button', { name, exact: true })).toBeDisabled();
    }
    await expect(page.getByRole('button', { name: 'Replay & compare hash' })).toBeEnabled();
    await expect(page.getByTestId('inject-inactive')).toContainText('needs a RUNNING run; this run is CANCELLED');
    await expect(page.getByTestId('api-error')).toHaveCount(0);

    // replay stays available on a finished run; the replay run is marked and not evaluated
    await page.getByRole('button', { name: 'Replay & compare hash' }).click();
    await replay.getByRole('link', { name: 'run_demo_replay' }).click();
    await expect(page.getByTestId('replay-badge')).toHaveText('Replay of run_demo');
    await expect(page.getByTestId('replay-notice')).toContainText('Replays are not evaluated');
    await expect(page.getByRole('link', { name: 'Evaluate and read results' })).toHaveCount(0);
  });

  test('a start rejected because the run changed elsewhere explains itself instead of a raw 409', async ({ page }) => {
    await page.goto('/scenarios/finance.invoice-bank-change/start');
    await page.getByLabel('Start the run immediately').uncheck();
    await page.getByRole('button', { name: 'Create run' }).click();
    await page.getByRole('link', { name: /Go to run/ }).click();
    await expect(page.getByTestId('controls-state')).toContainText('This run is READY');
    await expect(page.getByRole('button', { name: 'Advance clock' })).toBeDisabled();
    const runId = (page.url().match(/runs\/(run_\d+)/) as RegExpMatchArray)[1] as string;
    // someone else starts it
    await page.evaluate(async (id) => {
      await fetch(`/v1/runs/${id}/start`, { method: 'POST', headers: { Authorization: 'Bearer mk_controller' } });
    }, runId);
    await page.getByRole('button', { name: 'Start' }).click();
    await expect(page.getByTestId('control-note')).toContainText('Could not start the run: cannot start a RUNNING run.');
    await expect(page.getByTestId('api-error')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Start' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Advance clock' })).toBeEnabled();
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
