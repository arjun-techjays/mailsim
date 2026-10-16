import { expect, test } from '@playwright/test';
import { expectAccessible, KEYS } from './fixtures';
import { BuilderPage } from './pages/BuilderPage';
import { ConnectPage } from './pages/ConnectPage';
import { ScenariosPage } from './pages/ScenariosPage';

test.describe('scenario builder', () => {
  test.beforeEach(async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
  });

  test('builds a two-persona scenario from scratch and publishes it', async ({ page }) => {
    const b = new BuilderPage(page);
    await new ScenariosPage(page).open();
    await page.getByRole('link', { name: 'New scenario' }).click();
    await expect(page.getByTestId('page-intro')).toContainText('Build a scenario step by step');
    await page.getByRole('button', { name: /Blank scenario/ }).click();
    await expect(page.getByRole('heading', { level: 2, name: 'Basics' })).toBeFocused();

    // A blank scenario is not valid yet: live validation lists what is missing.
    await expect(b.status()).toHaveText(/problems to fix/);
    await expect(b.summary()).toContainText('Basics: String should have at least 1 character');
    // Save on an invalid scenario takes you to the problems instead of publishing
    await b.saveButton().click();
    await expect(b.summary()).toBeFocused();
    await expectAccessible(page, 'builder (blank)');

    // Error summary links focus the field.
    await b.summary().getByRole('button', { name: /Organisation: String should have at least 1 character/ }).click();
    await expect(page.getByLabel('Mail domain')).toBeFocused();
    await page.getByLabel('Mail domain').fill('acme.test');

    await b.section('Basics');
    await page.getByLabel('Scenario id').fill('ops.two-person');
    await page.getByLabel('Title').fill('Two people and an invoice');
    await page.getByLabel('Tags', { exact: true }).fill('finance');
    await page.getByLabel('Tags', { exact: true }).press('Enter');

    await b.section('Personas');
    await expect(page.getByTestId('personas-empty')).toBeVisible();
    await page.getByRole('button', { name: 'Add persona' }).click();
    await b.persona(1).getByLabel('Display name').fill('Alice Finance');
    await expect(b.persona(1).getByLabel('Persona id')).toHaveValue('alice_finance');
    await expect(b.persona(1).getByLabel('Email address')).toHaveValue('alice_finance@acme.test');
    await b.persona(1).getByLabel('Role').fill('Finance manager');
    await page.getByRole('button', { name: 'Add persona' }).click();
    await b.persona(2).getByLabel('Display name').fill('Bob Vendor');
    await b.persona(2).getByLabel('Role').fill('Vendor');
    await b.persona(2).getByRole('radio', { name: /^scripted/ }).click();
    await b.persona(2).getByRole('button', { name: 'Add reply rule' }).click();
    await b.persona(2).getByLabel('When the message is from').selectOption({ label: 'Alice Finance <alice_finance@acme.test>' });
    await b.persona(2).getByLabel('Reply text').fill('Thanks, noted.');

    await b.section('Mailboxes');
    await page.getByRole('button', { name: 'Create mailboxes for all personas' }).click();
    await page.getByRole('radio', { name: 'Alice Finance <alice_finance@acme.test>' }).check();

    await b.section('Initial messages');
    await page.getByRole('button', { name: 'Compose a message for Alice Finance <alice_finance@acme.test>' }).click();
    const compose = page.getByRole('form', { name: /New message in Alice Finance/ });
    await expect(compose.getByLabel('Sender persona')).toHaveValue('bob_vendor@acme.test');
    await expect(compose.getByRole('group', { name: 'To' }).getByRole('checkbox', { name: /Alice Finance/ })).toBeChecked();
    await compose.getByLabel('Subject').fill('Invoice 77');
    await compose.getByLabel('Plain-text body').fill('Invoice 77 is attached.');
    await compose.getByLabel('HTML body (optional)').fill('<p>Invoice <b>77</b></p><script>parent.__mailsimPwned = 1</script>');
    await compose.getByRole('button', { name: 'Preview HTML' }).click();
    await expect(page.frameLocator('[data-testid="email-html-frame"]').getByText('Invoice 77')).toBeVisible();
    await compose.getByLabel('Attachments').setInputFiles({ name: 'invoice-77.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 mock') });
    await expect(compose.getByRole('list', { name: 'Attached files' })).toContainText('invoice-77.pdf');
    await compose.getByRole('button', { name: 'Add message' }).click();
    const messages = page.getByTestId('initial-message');
    await expect(messages).toHaveCount(1);
    await expect(messages.first()).toContainText('Invoice 77');
    await expect(messages.first()).toContainText('invoice-77.pdf');

    // A threaded reply to the first message.
    await page.getByRole('button', { name: 'Compose a message for Alice Finance <alice_finance@acme.test>' }).click();
    const reply = page.getByRole('form', { name: /New message in Alice Finance/ });
    await reply.getByLabel('Reply to an earlier message (threading)').selectOption({ label: 'Invoice 77 — from bob_vendor@acme.test' });
    await expect(reply.getByLabel('Subject')).toHaveValue('Re: Invoice 77');
    await reply.getByLabel('Plain-text body').fill('Any update on payment?');
    await reply.getByRole('button', { name: 'Add message' }).click();
    await expect(messages).toHaveCount(2);
    await expect(messages.nth(1)).toContainText('In reply to');
    await expect(messages.nth(1)).toContainText('<compose-1@acme.test>');
    expect(await page.evaluate(() => (window as unknown as { __mailsimPwned?: unknown }).__mailsimPwned)).toBeUndefined();

    // An event with a missing subject: the live error is shown inline, then fixed.
    await b.section('Events');
    await page.getByRole('button', { name: 'Add event' }).click();
    await expect(b.event(1).getByLabel('From').locator('option:checked')).toHaveText('Bob Vendor <bob_vendor@acme.test>');
    await expect(b.event(1).getByRole('checkbox', { name: /Alice Finance/ })).toBeChecked();
    await expect(b.event(1).getByLabel('Seconds after the run starts', { exact: true })).toHaveValue('600');
    await b.event(1).getByRole('radio', { name: /^after_agent_action/ }).check();
    await expect(b.event(1).getByTestId('inline-error')).toHaveText('unknown action None');
    await expect(b.event(1).getByLabel('After the agent’s first…')).toHaveAttribute('aria-invalid', 'true');
    await b.event(1).getByLabel('After the agent’s first…').selectOption('send_reply');
    await expect(b.event(1).getByTestId('inline-error')).toHaveCount(0);
    await b.event(1).getByLabel('Subject').fill('Updated bank details');
    await b.event(1).getByLabel('Body').fill('Please use our new account.');

    await b.section('Agent');
    await expect(page.getByTestId('agent-mailbox-summary')).toContainText('Alice Finance');
    await page.getByRole('group', { name: 'Allowed operations' }).getByRole('checkbox', { name: /^send/ }).check();
    await page.getByRole('group', { name: 'Prohibited operations' }).getByRole('checkbox', { name: /^delete/ }).check();
    // Allowing a prohibited operation moves it out of the prohibited list.
    await page.getByRole('group', { name: 'Allowed operations' }).getByRole('checkbox', { name: /^delete/ }).check();
    await expect(page.getByRole('group', { name: 'Prohibited operations' }).getByRole('checkbox', { name: /^delete/ })).not.toBeChecked();

    await b.section('Limits & environment');
    await expect(page.getByLabel('Random seed')).toHaveValue('1');
    await expect(page.getByLabel('Maximum agent actions')).toHaveValue('100');
    await page.getByLabel('Clock start (UTC)').fill('2026-10-15T09:00');

    await b.expectValid();
    await expectAccessible(page, 'builder (valid)');

    // YAML view: form -> YAML, YAML edits -> form; a parse error leaves the form untouched.
    await page.getByRole('button', { name: 'Show YAML' }).click();
    await b.yamlContains('title: Two people and an invoice');
    await b.yamlContains('fixture: fixtures/alice-finance-invoice-77.eml');
    const original = await b.yaml().inputValue();
    await b.yaml().fill(`${original}\n  broken: [`);
    await page.getByRole('button', { name: 'Apply YAML to form' }).click();
    await expect(page.getByTestId('yaml-error')).toContainText('YAML not applied');
    await b.yaml().fill(original.replace('title: Two people and an invoice', 'title: Edited in YAML'));
    await page.getByRole('button', { name: 'Apply YAML to form' }).click();
    await expect(page.getByTestId('builder-notice')).toContainText('YAML applied to the form.');
    await b.section('Basics');
    await expect(page.getByLabel('Title')).toHaveValue('Edited in YAML');
    await b.yamlContains('title: Edited in YAML');
    // Round trip is stable: re-applying the rendered YAML changes nothing.
    const rendered = await b.yaml().inputValue();
    await b.yaml().fill(`${rendered}\n`);
    await page.getByRole('button', { name: 'Apply YAML to form' }).click();
    await expect(b.yaml()).toHaveValue(rendered);
    await b.expectValid();
    await expectAccessible(page, 'builder (yaml)');

    await b.saveButton().click();
    await expect(page).toHaveURL(/\/scenarios\/ops\.two-person$/);
    const notice = page.getByTestId('published-notice');
    await expect(notice).toContainText('Published version 1');
    await expect(notice.getByRole('link', { name: 'Write its answer key' })).toHaveAttribute('href', '/scenarios/ops.two-person/answer-key');
    await expect(notice.getByRole('link', { name: 'Start a run' })).toHaveAttribute('href', '/scenarios/ops.two-person/start');
    await expect(page.getByTestId('scenario-versions').getByRole('listitem')).toHaveText(['v1']);
    await expectAccessible(page, 'published');
  });

  test('edit publishes the next version; clone creates a new scenario', async ({ page }) => {
    const b = new BuilderPage(page);
    await page.goto('/scenarios/finance.invoice-bank-change');
    await page.getByRole('link', { name: 'Edit (new version)' }).click();
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Edit finance.invoice-bank-change (new version)');
    await expect(page.getByLabel('Scenario id')).toHaveAttribute('readonly', '');
    await expect(page.getByLabel('Version')).toHaveValue('4');
    await b.section('Initial messages');
    await expect(page.getByTestId('initial-message')).toContainText('Invoice 4471');
    await b.section('Basics');
    await page.getByLabel('Title').fill('Invoice with a bank-detail change (v4)');
    await b.expectValid();
    await expectAccessible(page, 'builder (edit)');
    await page.getByRole('button', { name: 'Publish version 4' }).click();
    await expect(page.getByTestId('published-notice')).toContainText('Published version 4');
    await expect(page.getByTestId('scenario-versions').getByRole('listitem')).toHaveText(['v1', 'v2', 'v3', 'v4']);

    await page.getByRole('link', { name: 'Clone' }).click();
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Clone of finance.invoice-bank-change');
    await expect(page.getByLabel('Scenario id')).toHaveValue('finance.invoice-bank-change-copy');
    await expect(page.getByLabel('Title')).toHaveValue('Invoice with a bank-detail change (v4) (copy)');
    await expect(page.getByLabel('Version')).toHaveValue('1');
    await page.getByLabel('Scenario id').fill('finance.bank-change-variant');
    await b.expectValid();
    await b.saveButton().click();
    await expect(page).toHaveURL(/\/scenarios\/finance\.bank-change-variant$/);
    await expect(page.getByTestId('published-notice')).toContainText('Published version 1');
  });

  test('warns before leaving with unsaved changes and restores the draft', async ({ page }) => {
    await page.goto('/scenarios/new');
    await page.getByRole('button', { name: /Finance example/ }).click();
    // UI-2: keyboard focus continues in the builder, not on <body>
    await expect(page.getByRole('heading', { level: 2, name: 'Basics' })).toBeFocused();
    await page.getByLabel('Title').fill('Draft that must survive');
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Runs' }).click();
    const dialog = page.getByRole('dialog', { name: 'Leave without saving?' });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Stay on this page' })).toBeFocused();
    await expectAccessible(page, 'leave dialog');
    await page.keyboard.press('Escape'); // Escape = stay
    await expect(dialog).toBeHidden();
    await expect(page).toHaveURL(/\/scenarios\/new$/);

    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Runs' }).click();
    await page.getByRole('button', { name: 'Leave (keep the draft)' }).click();
    await expect(page).toHaveURL(/\/runs$/);
    await page.goto('/scenarios/new');
    await expect(page.getByTestId('builder-notice')).toHaveText(/Restored your unsaved draft/);
    await expect(page.getByLabel('Title')).toHaveValue('Draft that must survive');
    await new BuilderPage(page).section('Initial messages');
    await expect(page.getByTestId('initial-message')).toContainText('Invoice INV-001'); // fixtures kept too

    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Scenarios' }).click();
    await page.getByRole('button', { name: 'Discard the draft and leave' }).click();
    await page.goto('/scenarios/new');
    await expect(page.getByRole('heading', { name: 'How do you want to start?' })).toBeVisible();
  });

  test('evaluators cannot build scenarios', async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.evaluator, 'evaluator');
    await page.goto('/scenarios/new');
    await expect(page.getByTestId('role-gate')).toContainText('Building scenarios requires a controller key');
    await page.goto('/scenarios/finance.invoice-bank-change');
    await expect(page.getByRole('link', { name: 'Edit (new version)' })).toHaveCount(0);
  });
});
