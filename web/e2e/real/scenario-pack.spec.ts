import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type APIRequestContext } from '@playwright/test';
import { answerKeyYaml, eml, scenarioYaml, TRICKY, writePack, zipPack } from '../packs';

// Real-stack QA D-1 check (local no-auth mode, like the QA repro): a leadership pack imported
// through the dashboard publishes exactly one message per fixture, and a run's mailbox has no
// duplicate Message-IDs. Start the API with `serve --local-no-auth` and run `npm run e2e:real`
// with MAILSIM_API_URL and MAILSIM_LOCAL_TOKEN=<token from the printed dashboard link>.
const API = process.env.MAILSIM_API_URL;
const TOKEN = process.env.MAILSIM_LOCAL_TOKEN;
test.skip(!API || !TOKEN, 'set MAILSIM_API_URL and MAILSIM_LOCAL_TOKEN (from the --local-no-auth server output) to run');
test.describe.configure({ mode: 'serial' });

const CBO = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'scenarios', 'leadership', 'cbo');
const RUN = Date.now().toString(36);
const headers = () => ({ 'X-Mailsim-Local-Token': TOKEN as string });

/** The CBO pack with its scenario id (and the answer key's) renamed, so every run is new. */
function cboPack(id: string): Record<string, string | Buffer> {
  const files: Record<string, string | Buffer> = {
    'scenario.yaml': readFileSync(join(CBO, 'scenario.yaml'), 'utf8').replace('id: leadership.cbo-inbox', `id: ${id}`),
    'answer_key.yaml': readFileSync(join(CBO, 'answer_key.yaml'), 'utf8').replace('scenario: leadership.cbo-inbox', `scenario: ${id}`),
  };
  for (const f of readdirSync(join(CBO, 'fixtures'))) files[`fixtures/${f}`] = readFileSync(join(CBO, 'fixtures', f));
  return files;
}

/** Starts a run of `id` and returns {persona id: Message-ID headers of its initial messages}. */
async function runMailboxes(request: APIRequestContext, id: string): Promise<Record<string, string[]>> {
  const created = await request.post(`/v1/scenarios/${id}/runs`, {
    headers: { ...headers(), 'Idempotency-Key': `${id}-run` },
    data: { agent_build: 'pack-import-e2e', start: true },
  });
  expect(created.status()).toBe(201);
  const runId = ((await created.json()) as { run: { id: string } }).run.id;
  const boxes = (await (await request.get(`/v1/runs/${runId}/mailboxes`, { headers: headers() })).json()) as { id: string; persona_id: string | null }[];
  const out: Record<string, string[]> = {};
  for (const box of boxes) {
    const page = await request.get(`/v1/runs/${runId}/mailboxes/${box.id}/messages?page_size=500`, { headers: headers() });
    expect(page.status()).toBe(200);
    const body = (await page.json()) as { items: { message_id_header: string }[]; next_cursor: string | null };
    expect(body.next_cursor).toBeNull();
    out[box.persona_id ?? box.id] = body.items.map((m) => m.message_id_header);
  }
  return out;
}

const unique = (ids: string[]) => new Set(ids).size;

test.beforeEach(async ({ page }) => {
  await page.goto(`/#local_token=${TOKEN}`);
  await expect(page.getByTestId('session-role')).toHaveText('operator (local no-auth)');
});

test('builder: the CBO pack pasted as YAML + its 32 fixtures uploaded = 32 messages, no duplicates', async ({ page, request }) => {
  test.setTimeout(240_000);
  const id = `e2e.cbo-builder-${RUN}`;
  const pack = cboPack(id);
  const fixtures = Object.keys(pack).filter((p) => p.startsWith('fixtures/'));
  expect(fixtures).toHaveLength(32);
  await page.goto('/scenarios/new');
  await page.getByRole('button', { name: /Blank scenario/ }).click();
  await page.getByRole('button', { name: 'Show YAML' }).click();
  await page.getByLabel('Scenario YAML').fill(pack['scenario.yaml'] as string);
  await page.getByRole('button', { name: 'Apply YAML to form' }).click();
  await expect(page.getByTestId('builder-notice')).toContainText('YAML applied to the form.');
  await page.getByRole('navigation', { name: 'Scenario sections' }).getByRole('button', { name: /^\d+\. Initial messages/ }).click();
  const box = page.getByRole('region', { name: /Mailbox of Natalie Okafor/ });
  await expect(box.getByText(/File missing/)).toHaveCount(32);
  await box.locator('input[type=file]').setInputFiles(fixtures.map((p) => ({ name: p.slice('fixtures/'.length), mimeType: 'message/rfc822', buffer: pack[p] as Buffer })));
  await expect(box.getByTestId('upload-report')).toContainText('Filled 32 message file(s) the scenario lists.', { timeout: 120_000 });
  await expect(box.getByText(/File missing/)).toHaveCount(0);
  await expect(box.getByTestId('initial-message')).toHaveCount(32);
  await expect(page.getByTestId('validation-status')).toHaveText('Valid: ready to save', { timeout: 30_000 });
  await page.getByRole('button', { name: 'Save and publish' }).click();
  await expect(page).toHaveURL(new RegExp(`/scenarios/${id.replace(/\./g, '\\.')}$`), { timeout: 30_000 });

  const boxes = await runMailboxes(request, id);
  expect(boxes.cbo).toHaveLength(32);
  expect(unique(boxes.cbo as string[])).toBe(32);
});

test('import: the CBO pack as a .zip publishes the scenario and its answer key; the run has 32 unique messages', async ({ page, request }) => {
  test.setTimeout(180_000);
  const id = `e2e.cbo-zip-${RUN}`;
  await page.goto('/scenarios/import');
  await page.getByLabel('Choose a .zip file').setInputFiles({ name: 'cbo.zip', mimeType: 'application/zip', buffer: zipPack('cbo', cboPack(id)) });
  await expect(page.getByTestId('pack-check')).toContainText('Message files32', { timeout: 60_000 });
  await expect(page.getByTestId('pack-errors')).toHaveCount(0);
  await page.getByRole('button', { name: 'Publish scenario and answer key' }).click();
  await expect(page.getByTestId('pack-published')).toContainText(`${id} version 1 is published. Answer key version 1 is published`, { timeout: 60_000 });
  const boxes = await runMailboxes(request, id);
  expect(boxes.cbo).toHaveLength(32);
  expect(unique(boxes.cbo as string[])).toBe(32);
});

test('import: a folder pack with tricky file names (trailing hyphens) keeps every name and message', async ({ page, request }) => {
  const id = `e2e.pack-tricky-${RUN}`;
  const dir = writePack('tricky', {
    'scenario.yaml': scenarioYaml(id, TRICKY, [TRICKY[0] as string]),
    'answer_key.yaml': answerKeyYaml(id),
    ...Object.fromEntries(TRICKY.map((p, i) => [p, eml(`${id}-${i}`, `Tricky ${i}`)])),
  });
  await page.goto('/scenarios/import');
  await page.getByLabel('Choose a folder').setInputFiles(dir);
  await expect(page.getByTestId('pack-check')).toContainText(`Message files${TRICKY.length}`);
  await page.getByRole('button', { name: 'Publish scenario and answer key' }).click();
  await expect(page.getByTestId('pack-published')).toContainText('Answer key version 1 is published');
  const boxes = await runMailboxes(request, id);
  expect(boxes.finance).toHaveLength(TRICKY.length); // one copy per fixture
  expect(unique(boxes.finance as string[])).toBe(TRICKY.length);
  expect(boxes.vendor).toHaveLength(1); // the same message in another mailbox is fine
});

test('import: the real server refuses a pack whose mailbox has a duplicate Message-ID', async ({ page, request }) => {
  const id = `e2e.pack-dup-${RUN}`;
  const zip = zipPack('dup', { 'scenario.yaml': scenarioYaml(id, TRICKY.slice(0, 2)), [TRICKY[0] as string]: eml(`${id}-x`, 'One'), [TRICKY[1] as string]: eml(`${id}-x`, 'Copy') });
  await page.goto('/scenarios/import');
  await page.getByLabel('Choose a .zip file').setInputFiles({ name: 'dup.zip', mimeType: 'application/zip', buffer: zip });
  await expect(page.getByTestId('pack-errors')).toContainText(`duplicate Message-ID <${id}-x@example.test>`);
  await expect(page.getByTestId('pack-errors')).toContainText(`same Message-ID as ${TRICKY[0]}`);
  await expect(page.getByRole('button', { name: 'Publish scenario' })).toBeDisabled();
  expect((await request.get(`/v1/scenarios/${id}`, { headers: headers() })).status()).toBe(404);
});
