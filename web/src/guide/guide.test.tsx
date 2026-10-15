import { renderHook, screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { RunOut, SystemStatus } from '../api/types';
import { useAgentKeys } from '../connect/agentKeys';
import { setMockLlm } from '../mocks/connect';
import { mockDb, server } from '../test/server';
import { OPERATOR_SESSION, renderApp, sessionFor } from '../test/utils';
import { CONCEPTS, concept } from './conceptList';
import { deriveProgress, isEvaluated } from './progress';
import { summarizeStatus } from './systemStatus';

const run = (id: string, over: Partial<RunOut> = {}): RunOut => ({
  id,
  status: 'RUNNING',
  epoch: 1,
  virtual_time: null,
  agent_actions: 0,
  replay_of: null,
  scenario_key: 'finance.invoice-bank-change',
  scenario_version: 3,
  agent_build: null,
  ...over,
});
const runsAre = (items: RunOut[]) => server.use(http.get('*/v1/runs', () => HttpResponse.json({ items, next_cursor: null })));
const state = (n: number) => screen.getByTestId(`step-${n}-state`);

describe('getting started', () => {
  it('controller: live progress, role notes and the current step', async () => {
    renderApp('/', sessionFor('controller'));
    expect(await screen.findByRole('heading', { level: 1, name: 'Test your email agent' })).toBeInTheDocument();
    await screen.findByText('Done', { selector: '[data-testid="step-1-state"]' });
    await screen.findByText('Done', { selector: '[data-testid="step-4-state"]' });
    expect(state(2)).toHaveTextContent('Not checked');
    expect(state(3)).toHaveTextContent('Done');
    expect(await screen.findByText('To do', { selector: '[data-testid="step-5-state"]' })).toBeInTheDocument();
    expect(state(6)).toHaveTextContent('To do');
    expect(screen.getByTestId('progress-text')).toHaveTextContent('3 of 7 steps done.');
    const step1 = screen.getByTestId('step-1');
    expect(within(step1).getByRole('link', { name: 'Create a scenario' })).toHaveAttribute('href', '/scenarios/new');
    expect(screen.getByTestId('step-2')).toHaveTextContent('Answer keys need an evaluator key');
    expect(within(screen.getByTestId('step-3')).getByRole('link', { name: 'Start a run' })).toHaveAttribute(
      'href',
      '/scenarios/finance.invoice-bank-change/start',
    );
    expect(within(screen.getByTestId('step-4')).getByRole('link', { name: 'Connect your agent' })).toHaveAttribute('href', '/runs/run_empty/connect');
    expect(screen.getByTestId('step-6')).toHaveTextContent('Evaluating needs an evaluator key');
    expect(screen.getByTestId('step-7')).toHaveTextContent('Comparing builds needs an evaluator key');
    // the first unfinished step is the current one
    expect(screen.getByTestId('step-2')).toHaveAttribute('aria-current', 'step');
  });

  it('evaluator: answer keys are checked; run steps need a controller', async () => {
    renderApp('/', sessionFor('evaluator'));
    expect(await screen.findByText('Done', { selector: '[data-testid="step-2-state"]' })).toBeInTheDocument();
    expect(within(screen.getByTestId('step-1')).getByRole('link', { name: 'Browse scenarios' })).toHaveAttribute('href', '/scenarios');
    expect(within(screen.getByTestId('step-2')).getByRole('link', { name: 'Write the answer key' })).toHaveAttribute(
      'href',
      '/scenarios/finance.invoice-bank-change/answer-key',
    );
    expect(screen.getByTestId('step-3')).toHaveTextContent('Starting runs needs a controller key.');
    expect(screen.getByTestId('step-4')).toHaveTextContent('Connecting an agent needs a controller key.');
    expect(within(screen.getByTestId('step-6')).getByRole('link', { name: 'Evaluate the run' })).toBeInTheDocument();
  });

  it('operator in an empty project: everything to do, with what comes first', async () => {
    runsAre([]);
    server.use(http.get('*/v1/scenarios', () => HttpResponse.json([])));
    mockDb.localNoAuth = true;
    renderApp('/', OPERATOR_SESSION);
    expect(await screen.findByText('To do', { selector: '[data-testid="step-1-state"]' })).toBeInTheDocument();
    expect(await screen.findByText('To do', { selector: '[data-testid="step-5-state"]' })).toBeInTheDocument();
    expect(screen.getByTestId('progress-text')).toHaveTextContent('0 of 7 steps done.');
    expect(screen.getByTestId('step-2')).toHaveTextContent('Create a scenario first.');
    expect(screen.getByTestId('step-3')).toHaveTextContent('Create a scenario first.');
    expect(screen.getByTestId('step-4')).toHaveTextContent('Start a run first.');
    expect(screen.getByTestId('step-5')).toHaveTextContent('Start a run first.');
    expect(screen.getByTestId('step-7')).toHaveTextContent('Evaluate runs of two builds first.');
    expect(screen.getByTestId('step-1')).toHaveAttribute('aria-current', 'step');
  });

  it('all done: evaluated runs of two builds, clock advanced', async () => {
    runsAre([
      run('run_b', { status: 'PASSED', agent_build: 'agent-v2', agent_actions: 3 }),
      run('run_a', { status: 'FAILED', agent_build: 'agent-v1', agent_actions: 2 }),
    ]);
    server.use(http.get('*/v1/runs/run_b/events', () => HttpResponse.json({ items: [{ sequence: 1 }], next_cursor: null })));
    mockDb.localNoAuth = true;
    renderApp('/', OPERATOR_SESSION);
    expect(await screen.findByText('7 of 7 steps done.')).toBeInTheDocument();
    expect(within(screen.getByTestId('step-7')).getByRole('link', { name: 'Compare builds' })).toHaveAttribute(
      'href',
      '/scenarios/finance.invoice-bank-change/compare',
    );
    expect(screen.queryByRole('listitem', { current: 'step' })).toBeNull();
  });

  it('unknown when the run list is unavailable', async () => {
    server.use(http.get('*/v1/runs', () => HttpResponse.json({ error: { code: 'NOT_FOUND', message: 'x', details: {}, request_id: 'r' } }, { status: 404 })));
    renderApp('/', sessionFor('controller'));
    expect(await screen.findByText('Not checked', { selector: '[data-testid="step-3-state"]' })).toBeInTheDocument();
    expect(state(5)).toHaveTextContent('Not checked');
  });

  it('answers the common questions', async () => {
    const { user } = renderApp('/', sessionFor('controller'));
    await user.click(await screen.findByText('Where do I create mailboxes for personas?'));
    expect(screen.getByText(/every persona gets its\s+own simulated mailbox/)).toBeVisible();
    expect(screen.getByText('How do I connect through the GSuite or Office 365 simulator?')).toBeInTheDocument();
  });

  it('progress rules', () => {
    expect(isEvaluated(run('r', { status: 'INCONCLUSIVE' }))).toBe(true);
    expect(isEvaluated(run('r', { status: 'CANCELLED' }))).toBe(false);
    expect(deriveProgress({})).toEqual({
      scenario: 'unknown',
      answerKey: 'unknown',
      run: 'unknown',
      connect: 'unknown',
      advance: 'unknown',
      evaluate: 'unknown',
      compare: 'unknown',
    });
    const same = deriveProgress({ runs: [run('a', { status: 'PASSED', agent_build: 'x' }), run('b', { status: 'FAILED', agent_build: 'x' })] });
    expect(same.evaluate).toBe('done');
    expect(same.compare).toBe('todo');
    expect(deriveProgress({ answerKeys: 0 }).answerKey).toBe('todo');
  });
});

describe('concepts', () => {
  it('glossary page lists every concept with an anchor', async () => {
    const { container } = renderApp('/concepts');
    expect(screen.getByRole('heading', { level: 1, name: 'Concepts' })).toBeInTheDocument();
    const g = screen.getByTestId('glossary');
    for (const c of CONCEPTS) {
      expect(within(g).getByText(c.term)).toBeInTheDocument();
      expect(container.querySelector(`#${c.id}`)).not.toBeNull();
    }
    expect(concept('checks').question).toBe('What are checks on / off?');
  });

  it('"What is this?" disclosures open inline', async () => {
    const { user } = renderApp('/scenarios', sessionFor('controller'));
    const what = await screen.findByTestId('what-is-scenario');
    await user.click(within(what).getByText('What is a scenario?'));
    expect(what).toHaveAttribute('open');
    expect(within(what).getByRole('link', { name: 'All concepts' })).toHaveAttribute('href', '/concepts#scenario');
  });
});

describe('header', () => {
  it('role-aware primary navigation', async () => {
    renderApp('/concepts', sessionFor('controller'));
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).getAllByRole('link').map((a) => a.textContent)).toEqual(['Home', 'Scenarios', 'Runs', 'Concepts', 'Connect']);
    expect(within(nav).getByRole('link', { name: 'Concepts' })).toHaveAttribute('aria-current', 'page');
    expect(await screen.findByTestId('status-pill')).toHaveTextContent('Key mode');
  });

  it('visitors and agents see Concepts and Connect only, no status pill', () => {
    renderApp('/concepts');
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).getAllByRole('link').map((a) => a.textContent)).toEqual(['Concepts', 'Connect']);
    expect(screen.queryByTestId('status-pill')).toBeNull();
  });

  it('status pill: LLM model and listeners down', async () => {
    setMockLlm(mockDb, 'anthropic', 'claude-sonnet');
    server.use(
      http.get('*/v1/system/status', () =>
        HttpResponse.json({
          version: 'x',
          local_no_auth: false,
          llm_provider: 'anthropic',
          llm_model: 'claude-sonnet',
          interfaces: [],
          listeners: { imap: { running: false, tls: 'none' }, smtp: { running: true, tls: 'none' } },
        } satisfies SystemStatus),
      ),
    );
    renderApp('/concepts', sessionFor('evaluator'));
    const pill = await screen.findByTestId('status-pill');
    expect(pill).toHaveTextContent('LLM: anthropic (claude-sonnet)');
    expect(pill).toHaveTextContent('IMAP down · SMTP up');
  });

  it('summarizes a status with missing listeners', () => {
    const s = summarizeStatus({ version: 'v', local_no_auth: true, llm_provider: 'stub', interfaces: [], listeners: {} });
    expect(s).toEqual({ mode: 'Local no-auth', llm: 'LLM: stub', listeners: 'IMAP down · SMTP down', listenersUp: false });
  });

  it('useAgentKeys needs its provider', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => renderHook(() => useAgentKeys())).toThrow('useAgentKeys must be used inside <AgentKeyProvider>');
  });
});

describe('pages without a backend', () => {
  it('not found offers ways out', () => {
    renderApp('/nope');
    expect(screen.getByTestId('page-intro')).toHaveTextContent('There is nothing at this address.');
    expect(within(screen.getByTestId('page-intro')).getByRole('link', { name: 'Concepts' })).toHaveAttribute('href', '/concepts');
  });

  it('agent key notice explains where the key goes', async () => {
    renderApp('/agent-key', sessionFor('agent'));
    expect(await screen.findByText(/as the\s+IMAP\/SMTP password/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Connect with a controller or evaluator key' })).toHaveAttribute('href', '/connect');
  });
});
