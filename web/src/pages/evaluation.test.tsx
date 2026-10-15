import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { EvaluationReport } from '../api/types';
import { buildReport } from '../mocks/db';
import { server } from '../test/server';
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
});
