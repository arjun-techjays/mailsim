import { loadRecentRuns, loadSession, rememberRun, saveSession, SESSION_STORAGE_KEY } from './storage';

const session = { baseUrl: 'http://api.test', apiKey: 'secret-key', role: 'controller' as const };

describe('session storage', () => {
  it('round-trips through sessionStorage only, never localStorage', () => {
    const localSet = vi.spyOn(Storage.prototype, 'setItem');
    saveSession(session);
    expect(loadSession()).toEqual(session);
    // every setItem call targeted sessionStorage
    for (const call of localSet.mock.contexts) expect(call).toBe(window.sessionStorage);
    expect(JSON.stringify(window.localStorage)).not.toContain('secret-key');
    expect(window.localStorage.length).toBe(0);
    saveSession(null);
    expect(sessionStorage.getItem(SESSION_STORAGE_KEY)).toBeNull();
    expect(loadSession()).toBeNull();
  });

  it('ignores corrupted or invalid entries', () => {
    sessionStorage.setItem(SESSION_STORAGE_KEY, '{not json');
    expect(loadSession()).toBeNull();
    sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify({ ...session, role: 'admin' }));
    expect(loadSession()).toBeNull();
    sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify({ ...session, apiKey: '' }));
    expect(loadSession()).toBeNull();
  });

  it('keeps working in memory when storage throws', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    expect(() => saveSession(session)).not.toThrow();
    expect(() => rememberRun({ id: 'r1', scenarioKey: null })).not.toThrow();
  });

  it('copes with sessionStorage being unavailable', () => {
    const spy = vi.spyOn(window, 'sessionStorage', 'get').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(loadSession()).toBeNull();
    expect(() => saveSession(session)).not.toThrow();
    expect(loadRecentRuns()).toEqual([]);
    spy.mockRestore();
  });
});

describe('recent runs', () => {
  it('stores ids only, most recent first, deduplicated, max 10', () => {
    for (let i = 0; i < 12; i += 1) rememberRun({ id: `run_${i}`, scenarioKey: 's' });
    rememberRun({ id: 'run_5', scenarioKey: 's' });
    const runs = loadRecentRuns();
    expect(runs).toHaveLength(10);
    expect(runs[0]?.id).toBe('run_5');
    expect(runs.filter((r) => r.id === 'run_5')).toHaveLength(1);
  });

  it('tolerates junk', () => {
    sessionStorage.setItem('mailsim.recentRuns', '{"x":1}');
    expect(loadRecentRuns()).toEqual([]);
    sessionStorage.setItem('mailsim.recentRuns', '[{"id":1},{"id":"ok"},null]');
    expect(loadRecentRuns()).toEqual([{ id: 'ok' }]);
    sessionStorage.setItem('mailsim.recentRuns', '{bad');
    expect(loadRecentRuns()).toEqual([]);
  });
});
