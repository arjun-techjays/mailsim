import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../test/server';
import { renderApp, sessionFor } from '../test/utils';
import { makeZip } from '../test/zip';
import { buildPack, PACK_LIMITS, readFolderPack } from './pack';

vi.setConfig({ testTimeout: 30_000 });
afterEach(() => server.events.removeAllListeners());

const enc = new TextEncoder();
const eml = (id: string, subject = 'Hello') => ['From: vendor@example.test', 'To: finance@example.test', `Subject: ${subject}`, `Message-ID: <${id}@example.test>`, '', 'Hi', ''].join('\r\n');

function scenarioYaml(id = 'pack.demo', fixtures = ['fixtures/001-deal-.eml', 'fixtures/002-b.eml'], version = 1): string {
  return [
    'schema_version: "1.1"',
    'scenario:',
    `  id: ${id}`,
    `  version: ${version}`,
    '  title: Pack demo',
    '  environment:',
    '    seed: 1',
    '    clock:',
    '      start_at: "2026-10-15T09:00:00Z"',
    '  organization:',
    '    domain: example.test',
    '  personas:',
    '    - id: finance',
    '      email: finance@example.test',
    '      role: Finance',
    '    - id: vendor',
    '      email: vendor@example.test',
    '      role: Vendor',
    '  mailboxes:',
    '    - owner: finance',
    '      initial_messages:',
    ...fixtures.map((f) => `        - fixture: ${f}`),
    '    - owner: vendor',
    '  agent:',
    '    mailbox: finance',
    '',
  ].join('\n');
}

function keyYaml(scenario = 'pack.demo', checksDefault = 'on', pinned: number[] = [], version = 1): string {
  return [
    'answer_key_version: "1.0"',
    'answer_key:',
    `  scenario: ${scenario}`,
    `  version: ${version}`,
    ...(pinned.length ? ['  scenario_versions:', ...pinned.map((v) => `    - ${v}`)] : []),
    `  checks_default: ${checksDefault}`,
    '  assertions:',
    '    - id: nothing-sent',
    '      type: state',
    '      requirement: Nothing is sent',
    '      check:',
    '        where:',
    '          folder: sent',
    '        count:',
    '          eq: 0',
    '',
  ].join('\n');
}

function folderFile(rel: string, body: string | Uint8Array): File {
  const f = new File([body as BlobPart], rel.slice(rel.lastIndexOf('/') + 1));
  Object.defineProperty(f, 'webkitRelativePath', { value: rel });
  return f;
}

const PACK = () => [
  folderFile('demo/scenario.yaml', scenarioYaml()),
  folderFile('demo/answer_key.yaml', keyYaml()),
  folderFile('demo/fixtures/001-deal-.eml', eml('one')),
  folderFile('demo/fixtures/002-b.eml', eml('two')),
  folderFile('demo/fixtures/unused.eml', eml('three')),
  folderFile('demo/pack.json', '{}'),
  folderFile('demo/.DS_Store', 'x'),
];

async function openImport(session = sessionFor('controller')) {
  const r = renderApp('/scenarios/import', session);
  await screen.findByRole('heading', { level: 1, name: 'Import a scenario pack' });
  return r;
}

describe('buildPack and readFolderPack', () => {
  const entry = (path: string, data: string | Uint8Array = 'x') => ({ path, data: typeof data === 'string' ? enc.encode(data) : data });

  it('finds the scenario folder, keeps fixture paths exactly and lists what it does not read', () => {
    const pack = buildPack([
      entry('zip/demo/scenario.yml', 'a: 1'),
      entry('zip/demo/answer_key.json', '{}'),
      entry('zip/demo/fixtures/x-.EML', 'mail'),
      entry('zip/demo/notes.txt'),
      entry('zip/other.eml'),
      entry('zip/demo/sub/answer_key.yaml'),
      entry('__MACOSX/zip/demo/._scenario.yml'),
    ]);
    expect(pack).toMatchObject({
      root: 'zip/demo/',
      scenarioFile: 'zip/demo/scenario.yml',
      source: 'a: 1',
      answerKeyFile: 'zip/demo/answer_key.json',
      answerKeySource: '{}',
      fixtures: { 'fixtures/x-.EML': 'bWFpbA==' },
      ignored: ['zip/demo/notes.txt', 'zip/other.eml', 'zip/demo/sub/answer_key.yaml'],
    });
    expect(buildPack([entry('scenario.json', '{}')])).toMatchObject({ root: '', answerKeyFile: null, answerKeySource: null, fixtures: {} });
  });

  it('refuses packs without exactly one scenario, with several keys, too many files or bad text', () => {
    expect(() => buildPack([entry('a.eml')])).toThrow('no scenario.yaml');
    expect(() => buildPack([entry('a/scenario.yaml'), entry('b/scenario.json')])).toThrow('several scenario files (a/scenario.yaml, b/scenario.json)');
    expect(() => buildPack([entry('scenario.yaml'), entry('answer_key.yaml'), entry('answer_key.json')])).toThrow('several answer keys');
    const many = Array.from({ length: PACK_LIMITS.maxFixtures + 1 }, (_, i) => entry(`f/${i}.eml`));
    expect(() => buildPack([entry('scenario.yaml'), ...many])).toThrow('more than 200 .eml files');
    expect(() => buildPack([entry('scenario.yaml', new Uint8Array([0xff, 0xfe]))])).toThrow('the scenario scenario.yaml is not UTF-8 text');
    expect(() => buildPack([entry('scenario.yaml', new Uint8Array(PACK_LIMITS.maxDocumentBytes + 1))])).toThrow('larger than 1 MB');
  });

  it('checks folder limits before reading files', async () => {
    const tooMany = Array.from({ length: PACK_LIMITS.maxEntries + 1 }, (_, i) => folderFile(`p/${i}.txt`, 'x'));
    await expect(readFolderPack(tooMany)).rejects.toThrow('the folder has 401 files');
    await expect(readFolderPack([folderFile('../scenario.yaml', 'x')])).rejects.toThrow('unsafe path in the folder');
    await expect(readFolderPack([folderFile('p/fixtures/a\u202egpj.eml', 'x')])).rejects.toThrow('unsafe path in the folder');
    // a git checkout: .git/ litter does not count toward the 400 files...
    const litter = Array.from({ length: 450 }, (_, i) => folderFile(`p/.git/objects/${i}`, 'x'));
    expect((await readFolderPack([...litter, folderFile('p/scenario.yaml', 'a: 1')])).map((e) => e.path)).toEqual(['p/scenario.yaml']);
    // ...but a huge folder is refused before anything is read
    const huge = Array.from({ length: PACK_LIMITS.maxFolderFiles + 1 }, (_, i) => ({ name: `${i}`, webkitRelativePath: `p/.git/${i}`, size: 1 }) as unknown as File);
    await expect(readFolderPack(huge)).rejects.toThrow(`the folder has ${PACK_LIMITS.maxFolderFiles + 1} files`);
    const big = folderFile('p/a.eml', 'x');
    Object.defineProperty(big, 'size', { value: PACK_LIMITS.maxEntryBytes + 1 });
    await expect(readFolderPack([big])).rejects.toThrow('p/a.eml is larger than');
    const half = () => {
      const f = folderFile(`p/${Math.random()}.eml`, 'x');
      Object.defineProperty(f, 'size', { value: PACK_LIMITS.maxEntryBytes });
      return f;
    };
    await expect(readFolderPack(Array.from({ length: 6 }, half))).rejects.toThrow('exceed');
    const plain = new File(['a: 1'], 'scenario.yaml'); // a single file picked without a folder
    expect((await readFolderPack([plain, folderFile('p/readme.md', 'x')])).map((e) => e.path)).toEqual(['p/readme.md', 'scenario.yaml']);
    // a file that is never read is only listed; an unsafe name is shown escaped
    const odd = 'p/notes\u202etxt.md';
    expect((await readFolderPack([folderFile(odd, 'x')])).map((e) => e.path)).toEqual([JSON.stringify(odd)]);
  });
});

describe('Import scenario pack page', () => {
  it('imports a folder: checks it, then publishes the scenario and its answer key (QA D-1)', async () => {
    const { user } = await openImport();
    await user.upload(screen.getByLabelText('Choose a folder'), PACK());
    const check = await screen.findByTestId('pack-check');
    expect(check).toHaveTextContent('pack.demo version 1: Pack demo');
    expect(check).toHaveTextContent('Message files3');
    expect(check).toHaveTextContent('demo/answer_key.yaml (its references are checked when the scenario is published)');
    expect(screen.getByTestId('pack-warnings')).toHaveTextContent('fixtures/unused.eml: not listed by any mailbox: not published');
    expect(screen.getByTestId('pack-ignored')).toHaveTextContent('demo/pack.json');
    expect(screen.getByTestId('pack-ignored')).not.toHaveTextContent('.DS_Store');
    await user.click(screen.getByRole('button', { name: 'Publish scenario and answer key' }));
    const done = await screen.findByTestId('pack-published');
    expect(done).toHaveTextContent('pack.demo version 1 is published. Answer key version 1 is published (hidden from agents).');
    expect(within(done).getByRole('link', { name: 'Start a run' })).toHaveAttribute('href', '/scenarios/pack.demo/start');
    expect(within(done).queryByRole('link', { name: 'Write its answer key' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Publish scenario/ })).toBeNull();
  });

  it('imports a .zip without an answer key', async () => {
    const zip = await makeZip([
      { name: 'demo/scenario.yaml', data: scenarioYaml('pack.zip') },
      { name: 'demo/fixtures/001-deal-.eml', data: eml('z1'), deflate: true },
      { name: 'demo/fixtures/002-b.eml', data: eml('z2'), deflate: true },
    ]);
    const { user } = await openImport();
    await user.upload(screen.getByLabelText('Choose a .zip file'), new File([zip as BlobPart], 'demo.zip', { type: 'application/zip' }));
    expect(await screen.findByTestId('pack-check')).toHaveTextContent('none in the pack');
    await user.click(screen.getByRole('button', { name: 'Publish scenario' }));
    const done = await screen.findByTestId('pack-published');
    expect(done).toHaveTextContent('pack.zip version 1 is published.');
    expect(within(done).getByRole('link', { name: 'Write its answer key' })).toBeInTheDocument();
  });

  it('blocks a pack with problems: duplicate Message-IDs, answer-key errors', async () => {
    const { user } = await openImport();
    const dup = [
      folderFile('demo/scenario.yaml', scenarioYaml()),
      folderFile('demo/fixtures/001-deal-.eml', eml('same')),
      folderFile('demo/fixtures/002-b.eml', eml('same')),
    ];
    await user.upload(screen.getByLabelText('Choose a folder'), dup);
    const errors = await screen.findByTestId('pack-errors');
    expect(errors).toHaveTextContent('1 problem(s) in the scenario');
    expect(errors).toHaveTextContent('duplicate Message-ID <same@example.test>');
    expect(screen.getByRole('button', { name: 'Publish scenario' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Publish scenario' })).toHaveAccessibleDescription('Fix the problems in the pack and choose it again.');
    expect(screen.getByTestId('pack-status')).toHaveTextContent('The pack has 1 problem(s).');

    // the scenario exists already: the answer key is fully checked before anything is published
    const existing = [folderFile('f/scenario.yaml', scenarioYaml('finance.invoice-bank-change')), folderFile('f/answer_key.yaml', keyYaml('finance.invoice-bank-change', 'maybe'))];
    await user.upload(screen.getByLabelText('Choose a folder'), [...existing, ...dup.slice(1)]);
    expect(await screen.findByTestId('pack-key-errors')).toHaveTextContent("answer_key.checks_default: Input should be 'on' or 'off'");
    expect(screen.getByRole('button', { name: 'Publish scenario and answer key' })).toBeDisabled();
  });

  it('publishes the scenario but reports an answer key that fails once the scenario exists', async () => {
    const { user } = await openImport();
    const files = PACK();
    files[1] = folderFile('demo/answer_key.yaml', keyYaml('pack.demo', 'maybe'));
    await user.upload(screen.getByLabelText('Choose a folder'), files);
    await user.click(await screen.findByRole('button', { name: 'Publish scenario and answer key' }));
    const done = await screen.findByTestId('pack-published');
    expect(done).toHaveTextContent('pack.demo version 1 is published.');
    expect(within(done).getByTestId('pack-key-errors')).toHaveTextContent('The answer key was not published');
    expect(within(done).getByRole('link', { name: 'Write its answer key' })).toBeInTheDocument();
  });

  it('reports request failures: publishing, publishing the key, checking the key', async () => {
    const { user } = await openImport();
    server.use(http.post('*/v1/scenarios', () => HttpResponse.json({ error: { code: 'CONFLICT', message: 'version exists', details: {}, request_id: 'r1' } }, { status: 409 })));
    await user.upload(screen.getByLabelText('Choose a folder'), PACK());
    await user.click(await screen.findByRole('button', { name: 'Publish scenario and answer key' }));
    expect(await screen.findByText(/^Could not publish:/)).toHaveTextContent('CONFLICT (HTTP 409)');
    expect(screen.getByTestId('pack-check')).toBeInTheDocument(); // the check stays visible
    server.resetHandlers();

    server.use(http.post('*/v1/scenarios/:key/answer-keys', () => HttpResponse.json({ error: { code: 'CONFLICT', message: 'key exists', details: {}, request_id: 'r2' } }, { status: 409 })));
    await user.click(screen.getByRole('button', { name: 'Publish scenario and answer key' }));
    const done = await screen.findByTestId('pack-published');
    expect(within(done).getByText(/^The answer key was not published:/)).toBeInTheDocument();
    expect(done).toHaveTextContent('key exists');

    server.use(http.post('*/v1/scenarios/:key/answer-keys/validate', () => HttpResponse.json({ error: { code: 'INTERNAL', message: 'boom', details: {}, request_id: 'r3' } }, { status: 500 })));
    await user.upload(screen.getByLabelText('Choose a folder'), PACK());
    expect(await screen.findByText(/^Could not read the pack:/)).toBeInTheDocument();
    expect(screen.getByTestId('api-error')).toHaveTextContent('boom');
  });

  it('explains packs it cannot read and ignores an empty choice', async () => {
    const { user } = await openImport();
    await user.upload(screen.getByLabelText('Choose a .zip file'), new File(['not a zip at all, sorry'], 'x.zip'));
    expect(await screen.findByText('Could not read the pack')).toBeInTheDocument();
    expect(screen.getByText(/not a .zip file/)).toBeInTheDocument();
    const huge = new File(['x'], 'huge.zip');
    Object.defineProperty(huge, 'size', { value: PACK_LIMITS.maxZipBytes + 1 });
    await user.upload(screen.getByLabelText('Choose a .zip file'), huge);
    expect(await screen.findByText(/huge.zip is larger than 30.0 MB/)).toBeInTheDocument();
    await user.upload(screen.getByLabelText('Choose a folder'), [folderFile('p/readme.md', 'x')]);
    expect(await screen.findByText(/no scenario.yaml/)).toBeInTheDocument();
    // nothing chosen: nothing happens
    const zipInput = screen.getByLabelText('Choose a .zip file');
    await user.upload(zipInput, []);
    await user.upload(screen.getByLabelText('Choose a folder'), []);
    expect(screen.getByText(/no scenario.yaml/)).toBeInTheDocument();
  });

  it('shows a document without id, version or title as such', async () => {
    const { user } = await openImport();
    await user.upload(screen.getByLabelText('Choose a folder'), [folderFile('p/scenario.yaml', 'just: text\n'), folderFile('p/answer_key.yaml', keyYaml())]);
    const check = await screen.findByTestId('pack-check');
    expect(check).toHaveTextContent('(no id) version ?');
    expect(screen.getByTestId('pack-errors')).toBeInTheDocument();
  });

  it('re-importing an existing scenario: a new version defers the key check; the same version checks it now', async () => {
    const { user } = await openImport();
    const F = 'finance.invoice-bank-change'; // seeded with versions 1-3
    const mail = [folderFile('f/fixtures/001-deal-.eml', eml('r1')), folderFile('f/fixtures/002-b.eml', eml('r2'))];
    // new version 4 whose key pins [4]: the API would call version 4 unknown until it is published
    await user.upload(screen.getByLabelText('Choose a folder'), [folderFile('f/scenario.yaml', scenarioYaml(F, undefined, 4)), folderFile('f/answer_key.yaml', keyYaml(F, 'on', [4], 9)), ...mail]);
    const check = await screen.findByTestId('pack-check');
    expect(check).toHaveTextContent('(its references are checked when the scenario is published)');
    expect(screen.queryByTestId('pack-key-errors')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Publish scenario and answer key' }));
    expect(await screen.findByTestId('pack-published')).toHaveTextContent(`${F} version 4 is published. Answer key version 9 is published`);
    await waitFor(() => expect(screen.getByRole('heading', { name: '3. Published' })).toHaveFocus());
    expect(screen.getByTestId('pack-status')).toHaveTextContent(`Published ${F} version 4. Answer key published.`);

    // the same (stored) version: the key is checked before publishing
    await user.upload(screen.getByLabelText('Choose a folder'), [folderFile('f/scenario.yaml', scenarioYaml(F)), folderFile('f/answer_key.yaml', keyYaml(F, 'on', [1], 9)), ...mail]);
    await waitFor(() => expect(screen.getByTestId('pack-check')).not.toHaveTextContent('(its references'));
    expect(screen.queryByTestId('pack-key-errors')).toBeNull();
    // ...unless the key also names a version that is not stored yet
    await user.upload(screen.getByLabelText('Choose a folder'), [folderFile('f/scenario.yaml', scenarioYaml(F)), folderFile('f/answer_key.yaml', keyYaml(F, 'on', [1, 7], 9)), ...mail]);
    await waitFor(() => expect(screen.getByTestId('pack-check')).toHaveTextContent('(its references are checked when the scenario is published)'));
    // a scenario lookup that fails for another reason is reported
    server.use(http.get('*/v1/scenarios/:key', () => HttpResponse.json({ error: { code: 'INTERNAL', message: 'lookup broke', details: {}, request_id: 'r9' } }, { status: 500 })));
    await user.upload(screen.getByLabelText('Choose a folder'), [folderFile('f/scenario.yaml', scenarioYaml(F)), folderFile('f/answer_key.yaml', keyYaml(F)), ...mail]);
    expect(await screen.findByText(/lookup broke/)).toBeInTheDocument();
    expect(screen.getByTestId('pack-status')).toHaveTextContent('Could not read the pack.');
  });

  it('is linked from the scenarios page and gated to controllers', async () => {
    const { user } = renderApp('/scenarios', sessionFor('controller'));
    await user.click(await screen.findByRole('link', { name: 'Import scenario pack' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Import a scenario pack' })).toBeInTheDocument();
  });

  it('tells evaluators they need a controller key', async () => {
    renderApp('/scenarios/import', sessionFor('evaluator'));
    expect(await screen.findByTestId('role-gate')).toHaveTextContent('Importing scenario packs requires a controller key');
    await waitFor(() => expect(screen.queryByLabelText('Choose a folder')).toBeNull());
  });
});
