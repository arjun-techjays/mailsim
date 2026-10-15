import { expect, type Locator, type Page } from '@playwright/test';

/** Page object for the scenario builder (/scenarios/new, /edit, /clone). */
export class BuilderPage {
  constructor(readonly page: Page) {}

  status(): Locator {
    return this.page.getByTestId('validation-status');
  }

  summary(): Locator {
    return this.page.getByTestId('error-summary');
  }

  async section(name: string) {
    await this.page.getByRole('navigation', { name: 'Scenario sections' }).getByRole('button', { name: new RegExp(`^\\d+\\. ${name}`) }).click();
    await expect(this.page.getByRole('heading', { level: 2, name, exact: true })).toBeFocused();
  }

  persona(n: number): Locator {
    return this.page.getByRole('group', { name: new RegExp(`^Persona ${n}:`) });
  }

  event(n: number): Locator {
    return this.page.getByRole('group', { name: new RegExp(`^Event ${n}:`) });
  }

  saveButton(): Locator {
    return this.page.getByRole('button', { name: /^(Save and publish|Publish version \d+)$/ });
  }

  async expectValid() {
    await expect(this.status()).toHaveText('Valid: ready to save');
    await expect(this.saveButton()).toBeEnabled();
  }

  yaml(): Locator {
    return this.page.getByLabel('Scenario YAML');
  }

  /** Waits until the YAML view shows the form's current document. */
  async yamlContains(text: string) {
    await expect(this.yaml()).toHaveValue(new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  }
}
