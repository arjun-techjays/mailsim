import { expect, test } from '@playwright/test';
import { expectAccessible, KEYS } from './fixtures';
import { answerKeyYaml, eml, scenarioYaml, TRICKY, writePack, zipPack } from './packs';
import { BuilderPage } from './pages/BuilderPage';
import { ConnectPage } from './pages/ConnectPage';

// QA D-1 (mock API): fixture uploads fill the files a pasted scenario lists by EXACT name and
// never add a second copy; a scenario pack (folder or .zip) is checked and published in one step.
const base = (p: string) => p.slice(p.lastIndexOf('/') + 1);
const files = (paths: string[]) => paths.map((p, i) => ({ name: base(p), mimeType: 'message/rfc822', buffer: Buffer.from(eml(`m${i}`, `Message ${i}`)) }));

test.describe('scenario packs', () => {
  test.beforeEach(async ({ page }) => {
    await new ConnectPage(page).connectAs(KEYS.controller, 'controller');
  });

  test('builder: uploads fill a pasted scenario by exact file name, no duplicates', async ({ page }) => {
    const b = new BuilderPage(page);
    await page.goto('/scenarios/new');
    await page.getByRole('button', { name: /Blank scenario/ }).click();
    await page.getByRole('button', { name: 'Show YAML' }).click();
    await b.yaml().fill(scenarioYaml('e2e.pack-builder', TRICKY));
    await page.getByRole('button', { name: 'Apply YAML to form' }).click();
    await expect(page.getByTestId('builder-notice')).toContainText('YAML applied to the form.');
    await b.section('Initial messages');
    const box = page.getByRole('region', { name: /Mailbox of finance/ });
    await expect(box.getByText(/File missing/)).toHaveCount(TRICKY.length);
    await expect(box.getByText(/File missing/).nth(1)).toContainText('003-the-expected-volume-on-the-deal-.eml');

    const stray = { name: 'stray.eml', mimeType: 'message/rfc822', buffer: Buffer.from(eml('stray', 'Stray')) };
    await box.getByLabel(/Upload .eml files for finance/).setInputFiles([...files(TRICKY), stray]);
    await expect(box.getByTestId('upload-report')).toContainText(`Filled ${TRICKY.length} message file(s) the scenario lists.`);
    await expect(box.getByText(/File missing/)).toHaveCount(0);
    await expect(box.getByTestId('initial-message')).toHaveCount(TRICKY.length);
    await expect(box.getByTestId('initial-message').nth(1)).toContainText('fixtures/003-the-expected-volume-on-the-deal-.eml');
    await expect(box.getByTestId('unmatched-uploads')).toContainText('stray.eml');
    await box.getByRole('button', { name: 'Discard' }).click();

    // uploading the same files again changes nothing
    await box.getByLabel(/Upload .eml files for finance/).setInputFiles(files(TRICKY));
    await expect(box.getByTestId('upload-report')).toContainText('already in the scenario as fixtures/001-hello.eml');
    await expect(box.getByTestId('initial-message')).toHaveCount(TRICKY.length);
    await b.expectValid();
    await expectAccessible(page, 'builder after matching uploads');
    await b.saveButton().click();
    await expect(page).toHaveURL(/\/scenarios\/e2e\.pack-builder$/);
  });

  test('imports a folder pack with an answer key', async ({ page }) => {
    const id = 'e2e.pack-folder';
    const dir = writePack('pack', {
      'scenario.yaml': scenarioYaml(id, TRICKY),
      'answer_key.yaml': answerKeyYaml(id),
      'pack.json': '{}',
      ...Object.fromEntries(TRICKY.map((p, i) => [p, eml(`f${i}`, `Folder ${i}`)])),
    });
    await page.goto('/scenarios');
    await page.getByRole('link', { name: 'Import scenario pack' }).click();
    await page.getByLabel('Choose a folder').setInputFiles(dir);
    const check = page.getByTestId('pack-check');
    await expect(check).toContainText(`${id} version 1: Pack import e2e`);
    await expect(check).toContainText(`Message files${TRICKY.length}`);
    await expect(page.getByTestId('pack-ignored')).toContainText('pack/pack.json');
    await expectAccessible(page, 'pack import (checked)');
    await page.getByRole('button', { name: 'Publish scenario and answer key' }).click();
    await expect(page.getByTestId('pack-published')).toContainText(`${id} version 1 is published. Answer key version 1 is published`);
    await page.getByRole('link', { name: 'Open the scenario' }).click();
    await expect(page).toHaveURL(new RegExp(`/scenarios/${id.replace('.', '\\.')}$`));
  });

  test('a .zip pack with a duplicate Message-ID is refused before anything is published', async ({ page }) => {
    const id = 'e2e.pack-dup';
    const zip = zipPack('dup', {
      'scenario.yaml': scenarioYaml(id, TRICKY.slice(0, 2)),
      [TRICKY[0] as string]: eml('same', 'One'),
      [TRICKY[1] as string]: eml('same', 'Copy of one'),
    });
    await page.goto('/scenarios/import');
    await page.getByLabel('Choose a .zip file').setInputFiles({ name: 'dup.zip', mimeType: 'application/zip', buffer: zip });
    await expect(page.getByTestId('pack-errors')).toContainText('duplicate Message-ID <same@example.test>');
    await expect(page.getByRole('button', { name: 'Publish scenario' })).toBeDisabled();
  });
});
