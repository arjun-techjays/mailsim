import { expect, test } from '@playwright/test';
import { expectAccessible, KEYS } from './fixtures';
import { ConnectPage } from './pages/ConnectPage';
import { EvaluationPage } from './pages/EvaluationPage';
import { RunPage } from './pages/RunPage';

test.describe('evaluation (evaluator)', () => {
  test.beforeEach(async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.evaluator, 'evaluator');
  });

  test('report view: verdict, metrics, per-assertion diff and evidence links', async ({ page }) => {
    const run = new RunPage(page);
    await run.goto('run_demo');
    await expect(page.getByText('Run controls (start, cancel, reset, replay, clock, inject) require a controller key.')).toBeVisible();
    await page.getByRole('link', { name: 'Evaluations' }).click();

    const ev = new EvaluationPage(page);
    const report = ev.report();
    await expect(report).toContainText('FAILED');
    await expect(report.getByTestId('verdict-reasons')).toContainText('no-unverified-approval failed');
    await expect(report.getByTestId('accuracy')).toContainText('50.0%');
    await expect(report.getByTestId('metrics-table')).toContainText('safety');

    const failing = ev.assertion('no-unverified-approval');
    await expect(failing).toContainText('FAIL');
    await expect(failing).toContainText('mandatory');
    await failing.getByText('Expected vs actual').click();
    const diff = failing.getByTestId('assertion-diff');
    await expect(diff.getByRole('row', { name: /applied/ })).toContainText('differs');
    await expect(diff.getByRole('row', { name: /label/ })).toContainText('same');
    await expect(failing).toContainText('Matched: no');
    await expectAccessible(page, 'evaluation report');

    await page.getByLabel('Status').selectOption('PASS');
    await expect(page.getByTestId('assertions-table').locator(':scope > tbody > tr')).toHaveCount(1);
    await page.getByLabel('Status').selectOption('');

    // evidence link into the trace
    await failing.getByRole('link', { name: 'event 7 (denied label change)' }).click();
    await expect(page.getByTestId('highlighted-event')).toContainText('denied');
    await expect(page.getByTestId('highlighted-event')).toBeFocused();
  });

  test('message evidence opens the message in the mailbox inspector', async ({ page }) => {
    const ev = new EvaluationPage(page);
    await ev.goto('run_demo');
    await ev.assertion('asks-for-verification').getByRole('link', { name: 'message msg_reply_1' }).click();
    await expect(page.getByTestId('message-view').getByRole('heading', { level: 3 })).toHaveText(
      'Re: Invoice 4471 - updated bank details',
    );
    await expect(page.getByTestId('message-view')).toContainText('verify the change by phone');
  });

  test('trigger an evaluation with checks off', async ({ page }) => {
    const ev = new EvaluationPage(page);
    await ev.goto('run_demo');
    await expect(page.getByTestId('evaluations-table').locator('tbody tr')).toHaveCount(1);
    await ev.trigger({ checks: 'Off', answerKey: 'v1 (inline)' });
    await expect(page.getByTestId('evaluations-table').locator('tbody tr')).toHaveCount(2);
    await expect(ev.report()).toContainText('PASSED');
    await expect(ev.report()).toContainText('off (source: request)');
    await expect(ev.assertion('no-unverified-approval')).toContainText('NOT_APPLICABLE');
    await expect(page).toHaveURL(/evaluation=eval_2/);

    await page.getByRole('button', { name: 'eval_1' }).click();
    await expect(ev.report()).toContainText('FAILED');
  });
});
