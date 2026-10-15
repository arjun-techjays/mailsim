import { clearDraft, DRAFT_BUDGET_CHARS, draftStorageKey, loadDraft, saveDraft, type Draft } from './draft';
import { ApiError } from '../api/client';
import { base64Size, bytesToBase64, contentHash, fileToBase64, fixturesBytes, formatBytes, safeFileName, textToBase64, uniqueFixturePath } from './files';
import { blankForm } from './model';
import { fieldId, findTarget, indexIssues, normalizePath, publishIssues, sectionFor } from './paths';
import { fromIsoInput, toIsoInput } from './time';

describe('paths', () => {
  it('normalizes server paths and maps them to sections', () => {
    expect(normalizePath('scenario.personas[1].email')).toBe('scenario.personas.1.email');
    expect(normalizePath('body.document.scenario.id')).toBe('scenario.id');
    expect(normalizePath('.scenario')).toBe('scenario');
    expect(fieldId('scenario.personas.1.email')).toBe('fld-scenario-personas-1-email');
    const cases: [string, string][] = [
      ['scenario.id', 'basics'],
      ['schema_version', 'basics'],
      ['scenario', 'basics'],
      ['scenario.organization.domain', 'organisation'],
      ['scenario.personas[0].email', 'personas'],
      ['scenario.relationships[0]', 'personas'],
      ['scenario.projects', 'personas'],
      ['scenario.mailboxes[0].owner', 'mailboxes'],
      ['scenario.mailboxes[0].initial_messages[1].fixture', 'messages'],
      ['scenario.fixtures', 'messages'],
      ['scenario.agent.mailbox', 'mailboxes'],
      ['scenario.agent', 'agent'],
      ['scenario.events[2].event.to', 'events'],
      ['scenario.environment.seed', 'limits'],
      ['scenario.evaluation.limits.max_agent_actions', 'limits'],
    ];
    for (const [path, section] of cases) expect([path, sectionFor(path)]).toEqual([path, section]);
    expect(indexIssues([{ path: 'scenario.events[0].id', message: 'x' }])).toEqual([
      { path: 'scenario.events[0].id', message: 'x', key: 'scenario.events.0.id', section: 'events' },
    ]);
  });

  it('finds the closest registered element', () => {
    document.body.innerHTML = '<div id="fld-scenario-events-0"></div>';
    expect(findTarget('scenario.events[0].event.to.str')?.id).toBe('fld-scenario-events-0');
    expect(findTarget('scenario.agent')).toBeNull();
    document.body.innerHTML = '';
  });
});

describe('publish issues', () => {
  const err = (details: Record<string, unknown>) => new ApiError({ kind: 'http', status: 400, code: 'INVALID_REQUEST', message: 'x', details });
  it('reads issues or errors from an error envelope', () => {
    expect(publishIssues(new Error('x'))).toEqual([]);
    expect(publishIssues(err({}))).toEqual([]);
    expect(publishIssues(err({ issues: [{ path: 'scenario.id', message: 'm' }, null] }))).toEqual([{ path: 'scenario.id', message: 'm' }]);
    expect(publishIssues(err({ errors: ['scenario.id: m', 'plain'] }))).toEqual([
      { path: 'scenario.id', message: 'm' },
      { path: 'scenario', message: 'plain' },
    ]);
  });
});

describe('files', () => {
  it('encodes, sizes and names files', async () => {
    expect(textToBase64('héllo')).toBe(btoa('hÃ©llo'));
    expect(bytesToBase64(new Uint8Array([0, 255]))).toBe('AP8=');
    expect(await fileToBase64(new Blob(['abc']))).toBe('YWJj');
    vi.spyOn(FileReader.prototype, 'readAsArrayBuffer').mockImplementation(function (this: FileReader) {
      this.onerror?.(new ProgressEvent('error') as ProgressEvent<FileReader>);
    });
    await expect(fileToBase64(new Blob(['x']))).rejects.toThrow('Could not read the file.');
    expect(base64Size('YWJj')).toBe(3);
    expect(base64Size('YWI=')).toBe(2);
    expect(base64Size('YQ==')).toBe(1);
    expect(base64Size('')).toBe(0);
    expect(formatBytes(10)).toBe('10 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(3 * 1024 * 1024)).toBe('3.0 MB');
    expect(safeFileName('Invoice #1 (final).EML')).toBe('invoice-1-final.eml');
    expect(safeFileName('README')).toBe('readme');
    expect(safeFileName('???.pdf')).toBe('message.pdf');
    expect(safeFileName('.hidden')).toBe('hidden');
    expect(uniqueFixturePath('a.eml', [])).toBe('fixtures/a.eml');
    expect(uniqueFixturePath('a.eml', ['fixtures/a.eml', 'fixtures/a-2.eml'])).toBe('fixtures/a-3.eml');
    expect(uniqueFixturePath('noext', ['fixtures/noext'])).toBe('fixtures/noext-2');
    expect(fixturesBytes({ a: 'YWJj', b: 'YQ==' }, ['a', 'a', 'b', 'missing'])).toBe(4);
    expect(contentHash('abc')).toBe(contentHash('abc'));
    expect(contentHash('A'.repeat(1000) + 'x' + 'A'.repeat(1000))).not.toBe(contentHash('A'.repeat(1000) + 'y' + 'A'.repeat(1000)));
  });
});

describe('time', () => {
  it('converts datetime-local values to ISO UTC and back', () => {
    expect(toIsoInput('2026-10-15T09:00:00Z')).toBe('2026-10-15T09:00');
    expect(toIsoInput('2026-10-15T09:00Z')).toBe('2026-10-15T09:00');
    expect(toIsoInput('2026-10-15T09:00:30.5Z')).toBe('2026-10-15T09:00:30');
    expect(toIsoInput('2026-10-15T09:00:00+02:00')).toBe('');
    expect(fromIsoInput('')).toBe('');
    expect(fromIsoInput('2026-10-15T09:00')).toBe('2026-10-15T09:00:00Z');
    expect(fromIsoInput('2026-10-15T09:00:30')).toBe('2026-10-15T09:00:30Z');
  });
});

describe('draft storage', () => {
  const draft = (fixtures: Record<string, string> = {}): Draft => ({ form: blankForm(), fixtures, info: {} });

  it('saves, loads and clears per mode and key', () => {
    const key = draftStorageKey('edit', 'a.b');
    expect(key).toBe('mailsim.builderDraft:edit:a.b');
    expect(loadDraft(key)).toBeNull();
    expect(saveDraft(key, draft({ 'f.eml': 'QQ==' }), 'base')).toEqual({ fixturesKept: true });
    expect(loadDraft(key)).toMatchObject({ baseline: 'base', fixturesKept: true, draft: { fixtures: { 'f.eml': 'QQ==' } } });
    // unchanged fixtures are not rewritten
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    expect(saveDraft(key, draft({ 'f.eml': 'QQ==' }), 'base2', false)).toEqual({});
    expect(setItem).toHaveBeenCalledTimes(1);
    setItem.mockRestore();
    sessionStorage.setItem(key, JSON.stringify({ form: blankForm() }));
    expect(loadDraft(key)).toMatchObject({ baseline: '', draft: { info: {} } });
    clearDraft(key);
    expect(loadDraft(key)).toBeNull();
  });

  it('drops fixtures over the budget and survives broken or unavailable storage', () => {
    const key = draftStorageKey('new', '');
    expect(saveDraft(key, draft({ 'big.eml': 'A'.repeat(DRAFT_BUDGET_CHARS) }), 'b')).toEqual({ fixturesKept: false });
    expect(loadDraft(key)).toMatchObject({ fixturesKept: false, draft: { fixtures: {} } });
    sessionStorage.setItem(key, '{not json');
    expect(loadDraft(key)).toBeNull();
    sessionStorage.setItem(key, '{"info":{}}');
    expect(loadDraft(key)).toBeNull();
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('disabled');
    });
    expect(saveDraft(key, draft(), 'b')).toEqual({ fixturesKept: false });
    expect(() => clearDraft(key)).not.toThrow();
  });
});
