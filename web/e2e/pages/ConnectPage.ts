import { expect, type Page } from '@playwright/test';

export class ConnectPage {
  constructor(readonly page: Page) {}

  async goto() {
    await this.page.goto('/connect');
    await expect(this.page.getByRole('heading', { name: 'Connect to mailsim' })).toBeVisible();
  }

  async connect(apiKey: string) {
    await this.page.getByLabel('API key').fill(apiKey);
    await this.page.getByRole('button', { name: 'Connect', exact: true }).click();
  }

  async connectAs(apiKey: string, expectedRole: string) {
    await this.goto();
    await this.connect(apiKey);
    await expect(this.page.getByTestId('detected-role')).toHaveText(expectedRole);
  }
}
