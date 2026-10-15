import { expect, type Page } from '@playwright/test';

export class EvaluationPage {
  constructor(readonly page: Page) {}

  async goto(runId: string) {
    await this.page.goto(`/runs/${runId}/evaluations`);
    await expect(this.page.getByRole('heading', { level: 1 })).toContainText(runId);
  }

  report() {
    return this.page.getByTestId('evaluation-report');
  }

  assertion(id: string) {
    return this.page.getByTestId(`assertion-${id}`);
  }

  async trigger(opts: { checks: 'On' | 'Off' | 'Run / answer-key default'; answerKey?: string }) {
    if (opts.answerKey) await this.page.getByLabel('Answer-key version').selectOption({ label: opts.answerKey });
    await this.page.getByRole('radio', { name: opts.checks }).check();
    await this.page.getByRole('button', { name: 'Run evaluation' }).click();
  }
}
