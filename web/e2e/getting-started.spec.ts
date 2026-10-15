import { expect, test } from '@playwright/test';
import { expectAccessible, KEYS } from './fixtures';
import { ConnectPage } from './pages/ConnectPage';

test.describe('getting started + concepts + theme', () => {
  test('the checklist reflects the project and every step leads somewhere', async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Home' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Test your email agent' })).toBeVisible();
    await expect(page.getByTestId('step-1-state')).toHaveText('Done');
    await expect(page.getByTestId('step-3-state')).toHaveText('Done');
    await expect(page.getByTestId('step-4-state')).toHaveText('Done');
    await expect(page.getByTestId('step-2-state')).toHaveText('Not checked');
    await expect(page.getByTestId('progress-text')).toHaveText('3 of 7 steps done.');

    // walk the steps that this role can act on
    await page.getByTestId('step-3').getByRole('link', { name: 'Start a run' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Start a run' })).toBeVisible();
    await page.goBack();
    await page.getByTestId('step-4').getByRole('link', { name: 'Connect your agent' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Connect your agent' })).toBeVisible();
    await page.goBack();
    await page.getByTestId('step-5').getByRole('link', { name: 'Open the run' }).click();
    await expect(page.getByTestId('run-summary')).toBeVisible();
    await page.goBack();

    // FAQ answers the two questions users asked
    await page.getByText('Where do I create mailboxes for personas?').click();
    await expect(page.getByText(/every persona gets its\s+own simulated mailbox/)).toBeVisible();
    await page.getByText('How do I connect through the GSuite or Office 365 simulator?').click();
    await expect(page.getByText(/Choose Gmail API \(GSuite simulator\)/)).toBeVisible();

    // inline glossary
    const what = page.getByTestId('step-1').getByTestId('what-is-persona');
    await what.getByText('What is a persona?').click();
    await expect(what).toContainText('Each one gets a mailbox when a run starts');
    await what.getByRole('link', { name: 'All concepts' }).click();
    await expect(page).toHaveURL(/\/concepts#persona$/);
    await expect(page.getByTestId('glossary')).toContainText('Virtual clock');
  });

  test('evaluators see answer-key progress and evaluation actions', async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.evaluator, 'evaluator');
    await page.goto('/');
    await expect(page.getByTestId('step-2-state')).toHaveText('Done');
    await page.getByTestId('step-6').getByRole('link', { name: 'Evaluate the run' }).click();
    await expect(page.getByRole('heading', { level: 1 })).toContainText('Evaluations for');
  });

  test('concepts page is public and lists the vocabulary', async ({ page }) => {
    await page.goto('/concepts');
    for (const term of ['Scenario', 'Persona', 'Mailbox', 'Run', 'Agent key', 'Answer key', 'Checks on / off', 'Virtual clock', 'Interfaces']) {
      await expect(page.getByTestId('glossary').getByText(term, { exact: true })).toBeVisible();
    }
    await expectAccessible(page, 'concepts');
  });

  test('theme toggle: system follows the OS, light/dark override it and persist', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.goto('/concepts');
    const bg = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    expect(await bg()).toBe('rgb(18, 20, 23)'); // dark tokens via prefers-color-scheme
    await page.getByLabel('Theme').selectOption('light');
    expect(await bg()).toBe('rgb(255, 255, 255)');
    await page.getByLabel('Theme').selectOption('dark');
    await page.emulateMedia({ colorScheme: 'light' });
    expect(await bg()).toBe('rgb(18, 20, 23)');
    await page.reload();
    await expect(page.getByLabel('Theme')).toHaveValue('dark');
    expect(await bg()).toBe('rgb(18, 20, 23)');
    await page.getByLabel('Theme').selectOption('system');
    expect(await bg()).toBe('rgb(255, 255, 255)');
  });

  for (const scheme of ['light', 'dark'] as const) {
    test(`home is accessible (${scheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
      await page.goto('/');
      await expect(page.getByTestId('step-4-state')).toHaveText('Done');
      await page.getByTestId('step-1').getByText('What is a persona?').click();
      await expectAccessible(page, `home ${scheme}`);
      await page.goto('/scenarios');
      await expect(page.getByRole('table')).toBeVisible();
      await expectAccessible(page, `scenarios ${scheme}`);
      await page.goto('/runs/run_demo');
      await expect(page.getByTestId('run-summary')).toBeVisible();
      await expectAccessible(page, `run ${scheme}`);
      await page.goto('/nope');
      await expectAccessible(page, `not found ${scheme}`);
    });
  }

  test('status pill shows mode, LLM and listeners', async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
    const pill = page.getByTestId('status-pill');
    await expect(pill).toContainText('Key mode');
    await expect(pill).toContainText('LLM: stub');
    await expect(pill).toContainText('IMAP up · SMTP up');
  });
});
