import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { rememberRun } from '../auth/storage';
import { mockDb, server } from '../test/server';
import { renderApp, sessionFor } from '../test/utils';

describe('Runs page', () => {
  it('marks replay runs when a list includes them', async () => {
    server.use(
      http.get('*/v1/runs', () =>
        HttpResponse.json({
          items: [
            { id: 'run_r', status: 'PASSED', epoch: 1, virtual_time: null, agent_actions: 0, replay_of: 'run_o' },
            { id: 'run_o', status: 'PASSED', epoch: 1, virtual_time: null, agent_actions: 0, replay_of: null },
          ],
          next_cursor: null,
        }),
      ),
    );
    renderApp('/runs', sessionFor('evaluator'));
    const table = await screen.findByTestId('runs-table');
    expect(within(table).getAllByTestId('replay-badge')).toHaveLength(1);
    expect(within(table).getByRole('row', { name: /run_r/ })).toHaveTextContent('Replay of run_o');
  });

  it('lists runs from GET /v1/runs', async () => {
    renderApp('/runs', sessionFor('evaluator'));
    const table = await screen.findByTestId('runs-table');
    expect(within(table).getByRole('link', { name: 'run_demo' })).toBeInTheDocument();
    expect(table).toHaveTextContent('finance.invoice-bank-change v3');
    expect(table).toHaveTextContent('READY');
  });

  it('replays are listed only on request (include_replays=true)', async () => {
    mockDb.runs.run_demo_replay = { ...structuredClone(mockDb.runs.run_demo!), run: { ...mockDb.runs.run_demo!.run, id: 'run_demo_replay', replay_of: 'run_demo', status: 'REPLAYED' } };
    const { user } = renderApp('/runs', sessionFor('evaluator'));
    const table = await screen.findByTestId('runs-table');
    expect(within(table).queryByRole('link', { name: 'run_demo_replay' })).toBeNull();
    await user.click(screen.getByLabelText(/Include replay runs/));
    const row = await screen.findByRole('row', { name: /run_demo_replay/ });
    expect(row).toHaveTextContent('Replay of run_demo');
    expect(row).toHaveTextContent('REPLAYED');
  });


  it('load more runs and its failure', async () => {
    const run = (id: string) => ({ id, status: 'RUNNING', epoch: 1, virtual_time: 'T', agent_actions: 0, replay_of: null, agent_build: 'b' });
    server.use(
      http.get('*/v1/runs', ({ request }) => {
        const c = new URL(request.url).searchParams.get('cursor');
        if (!c) return HttpResponse.json({ items: [run('r1')], next_cursor: 'c2' });
        if (c === 'c2') return HttpResponse.json({ items: [run('r2')], next_cursor: 'c3' });
        return HttpResponse.error();
      }),
    );
    const { user } = renderApp('/runs', sessionFor('controller'));
    await user.click(await screen.findByRole('button', { name: 'Load more runs' }));
    expect(await screen.findByRole('link', { name: 'r2' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Load more runs' }));
    expect(await screen.findByText(/Could not load more runs/)).toBeInTheDocument();
  });

  it('renders runs without scenario or build', async () => {
    server.use(
      http.get('*/v1/runs', () =>
        HttpResponse.json({
          items: [{ id: 'r9', status: 'READY', epoch: 1, virtual_time: null, agent_actions: 0, replay_of: null, agent_build: null }],
          next_cursor: null,
        }),
      ),
    );
    renderApp('/runs', sessionFor('controller'));
    const row = (await screen.findByRole('link', { name: 'r9' })).closest('tr') as HTMLElement;
    expect(within(row).getAllByText('—')).toHaveLength(3);
  });

  it('empty run list', async () => {
    server.use(http.get('*/v1/runs', () => HttpResponse.json({ items: [], next_cursor: null })));
    renderApp('/runs', sessionFor('controller'));
    expect(await screen.findByText(/No runs yet\./)).toBeInTheDocument();
  });

  it('other list errors are shown with the session list', async () => {
    server.use(http.get('*/v1/runs', () => HttpResponse.error()));
    renderApp('/runs', sessionFor('controller'));
    expect(await screen.findByText(/Could not list runs/)).toBeInTheDocument();
    expect(screen.getByTestId('empty-state')).toHaveTextContent('No runs opened yet.');
  });

  it('falls back to the tab list when GET /v1/runs is absent, and opens by id', async () => {
    server.use(
      http.get('*/v1/runs', () =>
        HttpResponse.json({ error: { code: 'NOT_FOUND', message: 'Not Found', details: {}, request_id: 'r' } }, { status: 404 }),
      ),
    );
    rememberRun({ id: 'run_demo', scenarioKey: 'finance.invoice-bank-change' });
    rememberRun({ id: 'run_other', scenarioKey: null });
    const { user, router } = renderApp('/runs', sessionFor('controller'));
    expect(await screen.findByTestId('runs-fallback')).toBeInTheDocument();
    const recent = screen.getByTestId('recent-runs');
    expect(within(recent).getAllByRole('listitem')).toHaveLength(2);
    expect(recent).toHaveTextContent('(finance.invoice-bank-change)');

    await user.click(screen.getByRole('button', { name: 'Open run' }));
    expect(screen.getByText('Enter a run id.')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Run id'), ' run_demo ');
    await user.click(screen.getByRole('button', { name: 'Open run' }));
    expect(router.state.location.pathname).toBe('/runs/run_demo');
  });

});
