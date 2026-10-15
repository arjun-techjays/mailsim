import { expect, type Page } from '@playwright/test';

export class ComparePage {
  constructor(readonly page: Page) {}

  async goto(scenarioKey: string) {
    await this.page.goto(`/scenarios/${scenarioKey}/compare`);
    await expect(this.page.getByRole('heading', { level: 1 })).toContainText('Build comparison');
  }

  table() {
    return this.page.getByTestId('comparison-table');
  }

  async compare(opts: { builds?: string; baseline?: string }) {
    if (opts.builds !== undefined) await this.page.getByLabel('Builds (comma-separated)').fill(opts.builds);
    if (opts.baseline !== undefined) await this.page.getByLabel('Baseline build').fill(opts.baseline);
    await this.page.getByRole('button', { name: 'Compare' }).click();
  }
}
