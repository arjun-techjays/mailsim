import { expect, type Page } from '@playwright/test';

export class RunPage {
  constructor(readonly page: Page) {}

  async goto(runId: string) {
    await this.page.goto(`/runs/${runId}`);
    await expect(this.page.getByRole('heading', { level: 1 })).toContainText(runId);
  }

  async tab(name: 'Overview' | 'Mailboxes' | 'Event trace' | 'Actions' | 'Artifacts') {
    await this.page.getByRole('navigation', { name: 'Run sections' }).getByRole('link', { name }).click();
  }

  summary() {
    return this.page.getByTestId('run-summary');
  }

  messageList() {
    return this.page.getByTestId('message-list');
  }

  async openMessage(subject: string) {
    await this.messageList().getByRole('button', { name: subject, exact: true }).first().click();
    await expect(this.page.getByTestId('message-view').getByRole('heading', { level: 3 })).toHaveText(subject);
  }

  controlResult() {
    return this.page.getByTestId('control-result');
  }

  async inject(fields: { from?: string; to?: string; subject?: string; text?: string; html?: string }) {
    const form = this.page.getByRole('form', { name: 'Inject message' });
    if (fields.from !== undefined) await form.getByLabel('From', { exact: true }).fill(fields.from);
    if (fields.to !== undefined) await form.getByLabel('To (comma-separated)').fill(fields.to);
    if (fields.subject !== undefined) await form.getByLabel('Subject').fill(fields.subject);
    if (fields.text !== undefined) await form.getByLabel('Text body').fill(fields.text);
    if (fields.html !== undefined) await form.getByLabel(/HTML body/).fill(fields.html);
    await form.getByRole('button', { name: 'Inject message' }).click();
  }

  async advance(seconds: number) {
    await this.page.getByLabel('Advance clock by (seconds)').fill(String(seconds));
    await this.page.getByRole('button', { name: 'Advance clock' }).click();
  }
}
