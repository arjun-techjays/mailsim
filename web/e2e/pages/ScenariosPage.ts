import { expect, type Page } from '@playwright/test';

export class ScenariosPage {
  constructor(readonly page: Page) {}

  async open() {
    await this.page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Scenarios' }).click();
    await expect(this.page.getByRole('heading', { level: 1, name: 'Scenarios' })).toBeVisible();
  }

  rows() {
    return this.page.getByRole('table').getByRole('row');
  }

  async filter(opts: { tag?: string; q?: string }) {
    if (opts.tag !== undefined) await this.page.getByLabel('Tag').fill(opts.tag);
    if (opts.q !== undefined) await this.page.getByLabel('Search').fill(opts.q);
    await this.page.getByRole('button', { name: 'Apply filters' }).click();
  }

  async openScenario(key: string) {
    await this.page.getByRole('link', { name: key }).click();
    await expect(this.page.getByRole('heading', { level: 1 })).toContainText(key);
  }
}
