import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { EvaluationReport } from '../api/types';
import { buildReport } from '../mocks/db';
import { mockDb, server } from '../test/server';
import { renderApp, sessionFor } from '../test/utils';

describe('Evaluation page', () => {
  it('shows the report: verdict, accuracy, metrics, diff, evidence links', async () => {
    const { user, router } = renderApp('/runs/run_demo/evaluations', sessionFor('evaluator'));
    const report = await screen.findByTestId('evaluation-report');
    expect(report).toHaveTextContent('FAILED');
    expect(within(report).getByTestId('verdict-reasons')).toHaveTextContent('no-unverified-approval failed');
    expect(within(report).getByTestId('accuracy')).toHaveTextContent('50.0%');
    expect(within(report).getByTestId('metrics-table')).toHaveTextContent('integration');
    expect(report).toHaveTextContent('5120 / 840');
    expect(report).toHaveTextContent('on (source: request)');
    expect(report).toHaveTextContent('sha256:ak2');
    const repro = within(report).getByRole('region', { name: 'Reproducibility details', hidden: true });
    expect(repro).toHaveAttribute('tabindex', '0');

    const failing = screen.getByTestId('assertion-no-unverified-approval');
    const diff = within(failing).getByTestId('assertion-diff');
    expect(within(diff).getByRole('row', { name: /applied/ })).toHaveTextContent('differs');
    expect(within(diff).getByRole('row', { name: /label/ })).toHaveTextContent('same');
    expect(failing).toHaveTextContent('Matched: no — An approval attempt was recorded before verification.');
    expect(within(failing).getByRole('link', { name: 'action 4 (modify_labels)' })).toHaveAttribute(
      'href',
      '/runs/run_demo?tab=actions&action=4',
    );
    const inconclusive = screen.getByTestId('assertion-reply-tone');
    expect(inconclusive).toHaveTextContent('No expected/actual values recorded.');
    expect(inconclusive).toHaveTextContent('Matched: not compared');
    expect(within(inconclusive).getByRole('link', { name: 'state messages/msg_reply_1' })).toHaveAttribute(
      'href',
      '/runs/run_demo?tab=mailboxes',
    );
    const passing = screen.getByTestId('assertion-asks-for-verification');
    expect(passing).toHaveTextContent('Matched: yes');

    await user.selectOptions(screen.getByLabelText('Status'), 'NOT_APPLICABLE');
    expect(screen.getByText('No assertions with this status.')).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Status'), '');

    await user.click(within(screen.getByTestId('assertion-no-unverified-approval')).getByRole('link', { name: /event 7/ }));
    expect(router.state.location.search).toBe('?tab=trace&seq=7');
    expect(await screen.findByTestId('highlighted-event')).toHaveTextContent('denied');
  });

  it('triggers an evaluation and selects the new report', async () => {
    let body: unknown = null;
    server.events.on('request:start', async ({ request }) => {
      if (request.method === 'POST') body = await request.clone().json();
    });
    const { user, router } = renderApp('/runs/run_demo/evaluations', sessionFor('evaluator'));
    await screen.findByTestId('evaluation-report');
    await user.selectOptions(await screen.findByLabelText('Answer-key version'), '1');
    await user.click(screen.getByRole('radio', { name: 'Off' }));
    await user.click(screen.getByLabelText(/Complete the run/));
    await user.click(screen.getByRole('button', { name: 'Run evaluation' }));
    await waitFor(() => expect(router.state.location.search).toBe('?evaluation=eval_2'));
    expect(body).toEqual({ answer_key_version: 1, checks: 'off', complete_run: false });
    await waitFor(() => expect(screen.getByTestId('evaluation-report')).toHaveTextContent('PASSED'));
    expect(within(screen.getByTestId('evaluations-table')).getAllByRole('row')).toHaveLength(3);
    expect(screen.queryByTestId('verdict-reasons')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'eval_1' }));
    expect(screen.getByTestId('evaluation-report')).toHaveTextContent('FAILED');
    server.events.removeAllListeners();
  });

  it('defaults and evaluation errors', async () => {
    let body: unknown = null;
    server.events.on('request:start', async ({ request }) => {
      if (request.method === 'POST') body = await request.clone().json();
    });
    server.use(
      http.post('*/v1/runs/:id/evaluations', () =>
        HttpResponse.json({ error: { code: 'CONFLICT', message: 'not finished', details: {}, request_id: 'r' } }, { status: 409 }),
      ),
    );
    const { user } = renderApp('/runs/run_demo/evaluations', sessionFor('evaluator'));
    await screen.findByTestId('evaluation-report');
    await user.click(screen.getByRole('radio', { name: 'On' }));
    await user.click(screen.getByRole('radio', { name: 'Run / answer-key default' }));
    await user.click(screen.getByRole('button', { name: 'Run evaluation' }));
    expect(await screen.findByText(/Evaluation failed/)).toBeInTheDocument();
    expect(body).toEqual({ answer_key_version: null, checks: null, complete_run: true });
    server.events.removeAllListeners();
  });

  it('empty evaluation list and a run without a scenario', async () => {
    server.use(
      http.get('*/v1/runs/:id/evaluations', () => HttpResponse.json([])),
      http.get('*/v1/runs/:id', () =>
        HttpResponse.json({ id: 'r', status: 'RUNNING', epoch: 1, virtual_time: null, agent_actions: 0, replay_of: null }),
      ),
    );
    renderApp('/runs/r/evaluations', sessionFor('evaluator'));
    expect(await screen.findByText(/This run has not been evaluated yet\./)).toBeInTheDocument();
    expect(screen.queryByTestId('evaluation-report')).toBeNull();
  });

  it('list, run and answer-key errors', async () => {
    server.use(
      http.get('*/v1/runs/:id/evaluations', () => HttpResponse.error()),
      http.get('*/v1/scenarios/:key/answer-keys', () => HttpResponse.error()),
    );
    renderApp('/runs/run_demo/evaluations', sessionFor('evaluator'));
    expect(await screen.findByText(/Could not load evaluations/)).toBeInTheDocument();
    expect(await screen.findByText(/Could not load answer keys/)).toBeInTheDocument();
  });

  it('run lookup failure', async () => {
    renderApp('/runs/run_boom/evaluations', sessionFor('evaluator'));
    expect(await screen.findByText(/Could not load run/)).toBeInTheDocument();
  });

  it('renders minimal reports (no answer key, scores, llm evidence, unknown categories)', async () => {
    const base = buildReport('run_demo', 'eval_min', { checks: true, answerKeyVersion: 1 });
    // defaulted fields omitted on purpose: the UI must tolerate sparse payloads
    const minimal = {
      ...base,
      answer_key_version: null,
      answer_key_hash: null,
      agent_build: null,
      checks_enabled: false,
      verdict: 'INCONCLUSIVE',
      verdict_reasons: undefined,
      metrics: { custom: { passed: 1 } },
      accuracy: { overall: null },
      reproducibility: undefined,
      outcomes: [
        {
          assertion_id: 'judge',
          type: 'semantic',
          category: 'communication',
          severity: 'advisory',
          subject: 'agent',
          status: 'PASS',
          score: 0.875,
          evidence: [{ kind: 'llm', ref: 'call_1' }],
        },
      ],
    } as unknown as EvaluationReport;
    server.use(http.get('*/v1/runs/:id/evaluations', () => HttpResponse.json([minimal])));
    renderApp('/runs/run_demo/evaluations', sessionFor('evaluator'));
    const report = await screen.findByTestId('evaluation-report');
    expect(report).toHaveTextContent('INCONCLUSIVE');
    expect(report).toHaveTextContent('score 0.88');
    expect(within(report).getByText('llm call_1')).toBeInTheDocument();
    expect(within(report).getByTestId('metrics-table')).toHaveTextContent('custom');
    expect(within(report).getByTestId('accuracy')).toHaveTextContent('0 / 0');
    expect(report).toHaveTextContent('none');
    const row = screen.getByTestId('assertion-judge');
    expect(row).toHaveTextContent('—');
  });

  it('controller keys see a role gate and no evaluator requests are made', async () => {
    const seen: string[] = [];
    server.events.on('request:start', ({ request }) => {
      seen.push(request.url);
    });
    renderApp('/runs/run_demo/evaluations', sessionFor('controller'));
    expect(await screen.findByTestId('role-gate')).toHaveTextContent('Evaluation results requires an evaluator key');
    expect(screen.getByTestId('role-gate')).toHaveTextContent('connected as controller');
    expect(seen.filter((u) => !u.endsWith('/v1/system/status'))).toEqual([]); // header status pill only
    server.events.removeAllListeners();
  });

  describe('slow evaluate (UI-9) and replays', () => {
    afterEach(() => {
      vi.unstubAllEnvs();
      server.events.removeAllListeners();
    });

    it('a timed-out evaluation is retried with the same key and not duplicated', async () => {
      vi.stubEnv('VITE_LONG_REQUEST_TIMEOUT_MS', '300');
      mockDb.runs.run_demo!.run.agent_build = 'slow';
      const keys: (string | null)[] = [];
      server.events.on('request:start', ({ request }) => {
        if (request.method === 'POST' && request.url.endsWith('/evaluations')) keys.push(request.headers.get('idempotency-key'));
      });
      const { user, router } = renderApp('/runs/run_demo/evaluations', sessionFor('evaluator'));
      await screen.findByTestId('evaluation-report');
      await user.click(screen.getByRole('button', { name: 'Run evaluation' }));
      expect(await screen.findByText(/can take a minute/)).toBeInTheDocument();
      const notice = await screen.findByTestId('unconfirmed');
      expect(notice).toHaveTextContent('The evaluation was not confirmed');
      expect(screen.getByLabelText('Answer-key version')).toBeDisabled();
      // the list was refreshed: the stored evaluation already shows up
      await waitFor(() => expect(within(screen.getByTestId('evaluations-table')).getAllByRole('row')).toHaveLength(3));
      await user.click(within(notice).getByRole('button', { name: 'Check again' }));
      await waitFor(() => expect(router.state.location.search).toBe('?evaluation=eval_2'));
      expect(keys).toHaveLength(2);
      expect(keys[1]).toBe(keys[0]);
      expect(mockDb.runs.run_demo!.evaluations).toHaveLength(2); // stored once
      expect(screen.queryByTestId('unconfirmed')).toBeNull();
    });

    it('an incompatible answer key and a replay refused by the server are explained (EV-4)', async () => {
      mockDb.runs.run_demo!.run.scenario_version = 1;
      const { user } = renderApp('/runs/run_demo/evaluations', sessionFor('evaluator'));
      await screen.findByRole('option', { name: /^v2/ });
      await user.selectOptions(screen.getByLabelText('Answer-key version'), '2');
      await user.click(screen.getByRole('button', { name: 'Run evaluation' }));
      expect(await screen.findByTestId('evaluation-conflict')).toHaveTextContent('Answer key v2 covers scenario v2, v3, but this run uses scenario v1.');
      expect(screen.getByRole('link', { name: 'write an answer key that covers it' })).toHaveAttribute('href', '/scenarios/finance.invoice-bank-change/answer-key');
      // the server is the enforcement point for replays even when the page did not know
      mockDb.runs.run_demo!.run.replay_of = 'run_orig';
      await user.click(screen.getByRole('button', { name: 'Run evaluation' }));
      await waitFor(() => expect(screen.getByTestId('evaluation-conflict')).toHaveTextContent('replays are not evaluated'));
    });

    it('an unconfirmed evaluation can be discarded to start over', async () => {
      vi.stubEnv('VITE_LONG_REQUEST_TIMEOUT_MS', '300');
      mockDb.runs.run_demo!.run.agent_build = 'slow';
      const { user } = renderApp('/runs/run_demo/evaluations', sessionFor('evaluator'));
      await screen.findByTestId('evaluation-report');
      await user.click(screen.getByRole('button', { name: 'Run evaluation' }));
      const notice = await screen.findByTestId('unconfirmed');
      await user.click(within(notice).getByRole('button', { name: 'Discard and start over' }));
      await user.click(within(notice).getByRole('button', { name: 'Start over anyway' }));
      expect(screen.queryByTestId('unconfirmed')).toBeNull();
      expect(screen.getByRole('button', { name: 'Run evaluation' })).toBeEnabled();
    });

    it('a replay run is not offered for evaluation', async () => {
      mockDb.runs.run_demo!.run.replay_of = 'run_orig';
      renderApp('/runs/run_demo/evaluations', sessionFor('evaluator'));
      const note = await screen.findByTestId('replay-not-evaluated');
      expect(note).toHaveTextContent('This run is a replay of run_orig');
      expect(within(note).getByRole('link', { name: 'Evaluate the original run' })).toHaveAttribute('href', '/runs/run_orig/evaluations');
      expect(screen.queryByRole('button', { name: 'Run evaluation' })).toBeNull();
    });

    it('no compatible answer key for "latest compatible" is explained with the keys that exist', async () => {
      mockDb.runs.run_demo!.run.agent_build = 'no-key';
      const { user } = renderApp('/runs/run_demo/evaluations', sessionFor('evaluator'));
      await screen.findByTestId('evaluation-report');
      await user.click(screen.getByRole('button', { name: 'Run evaluation' }));
      const note = await screen.findByTestId('evaluation-conflict');
      expect(note).toHaveAttribute('role', 'alert');
      expect(note).toHaveTextContent(
        'No answer key covers scenario v3, the version this run uses (existing: v1 (covers scenario v1, v2); v2 (covers scenario v2)).',
      );
    });
  });
});
