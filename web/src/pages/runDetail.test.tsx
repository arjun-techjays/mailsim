import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { loadRecentRuns } from '../auth/storage';
import { mockDb, server } from '../test/server';
import { renderApp, sessionFor } from '../test/utils';

const rows = (testId: string) => within(screen.getByTestId(testId)).getAllByRole('row').slice(1);

describe('Run detail', () => {
  it('shows the summary and remembers the run', async () => {
    const { user } = renderApp('/runs/run_demo', sessionFor('controller'));
    const summary = await screen.findByTestId('run-summary');
    expect(summary).toHaveTextContent('RUNNING');
    expect(summary).toHaveTextContent('agent-v2');
    expect(within(summary).getByRole('link', { name: 'finance.invoice-bank-change v3' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Overview' })).toHaveAttribute('aria-current', 'page');
    await waitFor(() => expect(loadRecentRuns()[0]?.id).toBe('run_demo'));
    await user.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(screen.getByTestId('run-summary')).toBeInTheDocument();
  });

  it('state request failure', async () => {
    server.use(
      http.get('*/v1/runs/:id/state', () =>
        HttpResponse.json({ error: { code: 'INTERNAL', message: 'x', details: {}, request_id: 'r' } }, { status: 500 }),
      ),
    );
    renderApp('/runs/run_demo?tab=mailboxes', sessionFor('evaluator'));
    expect(await screen.findByText(/Could not load run state/)).toBeInTheDocument();
  });

  it('event trace: cursor pagination, server-side filters and highlight', async () => {
    const urls: string[] = [];
    server.events.on('request:start', ({ request }) => {
      urls.push(request.url);
    });
    const { user } = renderApp('/runs/run_demo?tab=trace&seq=110', sessionFor('evaluator'));
    expect(await screen.findByText(/Showing 50 of 50 loaded event\(s\); more pages available/)).toBeInTheDocument();
    expect(screen.getByText(/Event #110 is not loaded yet/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Load more events' }));
    await screen.findByText(/Showing 100 of 100/);
    await user.click(screen.getByRole('button', { name: 'Load more events' }));
    await screen.findByText(/Showing 120 of 120 loaded event\(s\)\./);
    expect(screen.queryByRole('button', { name: 'Load more events' })).toBeNull();
    const hl = screen.getByTestId('highlighted-event');
    expect(hl).toHaveTextContent('110');
    expect(hl).toHaveFocus();

    await user.selectOptions(screen.getByLabelText('Actor'), 'controller');
    await screen.findByText(/Showing 24 of 24/);
    expect(rows('event-table')).toHaveLength(24);
    expect(urls.at(-1)).toContain('actor=controller');
    expect(screen.getByLabelText('Actor')).toHaveFocus();
    await user.selectOptions(screen.getByLabelText('Epoch'), '2');
    await screen.findByText(/Showing 4 of 4/);
    await user.selectOptions(screen.getByLabelText('Type'), 'message.read');
    expect(await screen.findByTestId('empty-state')).toHaveTextContent('No events match these filters.');
    server.events.removeAllListeners();
  });

  it('filters are also applied client-side when the server ignores them', async () => {
    const { mockDb } = await import('../test/server');
    const all = mockDb.runs.run_demo?.events ?? [];
    server.use(
      http.get('*/v1/runs/:id/events', () => HttpResponse.json({ items: all.slice(0, 30), next_cursor: null })),
    );
    const { user } = renderApp('/runs/run_demo?tab=trace', sessionFor('evaluator'));
    await screen.findByText(/Showing 30 of 30/);
    await user.selectOptions(screen.getByLabelText('Actor'), 'controller');
    await screen.findByText(/Showing 6 of 30/);
  });

  it('empty trace', async () => {
    renderApp('/runs/run_empty?tab=trace', sessionFor('evaluator'));
    expect(await screen.findByTestId('empty-state')).toHaveTextContent('This run has no events yet.');
  });

  it('trace errors (first page and later page)', async () => {
    server.use(
      http.get('*/v1/runs/:id/events', ({ request }) =>
        new URL(request.url).searchParams.get('cursor')
          ? HttpResponse.json({ error: { code: 'INTERNAL', message: 'page', details: {}, request_id: 'r' } }, { status: 500 })
          : HttpResponse.json({
              items: [{ sequence: 1, epoch: 1, type: 't', actor: 'a', operation: 'o', outcome: 'ok', virtual_time: null, payload: {} }],
              next_cursor: 'x',
            }),
      ),
    );
    const { user } = renderApp('/runs/run_demo?tab=trace&seq=abc', sessionFor('evaluator'));
    await user.click(await screen.findByRole('button', { name: 'Load more events' }));
    expect(await screen.findByText(/Could not load more events/)).toBeInTheDocument();
  });

  it('trace error on the first page', async () => {
    server.use(http.get('*/v1/runs/:id/events', () => HttpResponse.error()));
    renderApp('/runs/run_demo?tab=trace', sessionFor('evaluator'));
    expect(await screen.findByText(/Could not load events/)).toBeInTheDocument();
  });

  it('actions table highlights by action id, and artifacts', async () => {
    const { user } = renderApp('/runs/run_demo?tab=actions&action=4', sessionFor('evaluator'));
    expect(await screen.findByTestId('actions-table')).toBeInTheDocument();
    expect(rows('actions-table')).toHaveLength(4);
    expect(screen.getByTestId('highlighted-action')).toHaveTextContent('modify_labels');
    expect(screen.getByTestId('highlighted-action')).toHaveFocus();
    expect(rows('actions-table')[2]).toHaveTextContent('#3–#4');
    expect(rows('actions-table')[0]).toHaveTextContent(/#1(?!–)/);
    expect(rows('actions-table')[3]).toHaveTextContent('#7');
    expect(rows('actions-table')[3]).not.toHaveTextContent('#7–#7');

    expect(screen.queryByTestId('unmatched-action')).toBeNull();
    await user.click(screen.getByRole('link', { name: 'Artifacts' }));
    expect(await screen.findByTestId('artifacts-table')).toHaveTextContent('trace/run_demo.jsonl');
    expect(screen.getByTestId('artifacts-table')).toHaveTextContent('unknown');
  });

  it('artifacts download through the API with the operator key; the trace has no file', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:a');
    URL.revokeObjectURL = vi.fn();
    const anchors: HTMLAnchorElement[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      anchors.push(this);
    });
    const { user } = renderApp('/runs/run_demo?tab=artifacts', sessionFor('evaluator'));
    const table = await screen.findByTestId('artifacts-table');
    expect(within(table).getByRole('row', { name: /trace\/run_demo\.jsonl/ })).toHaveTextContent('—');
    await user.click(within(table).getByRole('button', { name: 'Download blob/att/att_1' }));
    await waitFor(() => expect(anchors[0]?.download).toBe('att_1'));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:a');
  });

  it('artifact downloads: fallback name, errors, and foreign URLs refused', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:a');
    URL.revokeObjectURL = vi.fn();
    const anchors: HTMLAnchorElement[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      anchors.push(this);
    });
    server.use(
      http.get('*/v1/runs/:id/artifacts', () =>
        HttpResponse.json([
          { kind: 'raw_mime', ref: 'blob/raw/x.eml', content_type: 'message/rfc822', size: 1, url: '/v1/runs/run_demo/artifacts/blob/raw/x.eml' },
          { kind: 'report', ref: 'r', content_type: 'application/json', size: 1, url: 'https://evil.example/steal' },
          { kind: 'report', ref: 'gone', content_type: 'application/json', size: 1, url: '/v1/runs/run_demo/artifacts/gone' },
        ]),
      ),
      http.get('*/v1/runs/:id/artifacts/blob/raw/x.eml', () => new HttpResponse(new Uint8Array([1]))),
    );
    const { user } = renderApp('/runs/run_demo?tab=artifacts', sessionFor('evaluator'));
    await user.click(await screen.findByRole('button', { name: 'Download blob/raw/x.eml' }));
    await waitFor(() => expect(anchors[0]?.download).toBe('x.eml'));
    await user.click(screen.getByRole('button', { name: 'Download r' }));
    expect(await screen.findByText(/Refusing to download from https:\/\/evil\.example\/steal/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Download gone' }));
    expect(await screen.findByText(/Could not download gone/)).toBeInTheDocument();
  });

  it('empty actions/artifacts and their errors', async () => {
    server.use(
      http.get('*/v1/runs/:id/actions', () => HttpResponse.json([])),
      http.get('*/v1/runs/:id/artifacts', () => HttpResponse.json([])),
    );
    const { user } = renderApp('/runs/run_demo?tab=actions', sessionFor('evaluator'));
    expect(await screen.findByText('No actions recorded.')).toBeInTheDocument();
    await user.click(screen.getByRole('link', { name: 'Artifacts' }));
    expect(await screen.findByText(/No artifacts yet\./)).toBeInTheDocument();
  });

  it('actions/artifacts errors', async () => {
    server.use(
      http.get('*/v1/runs/:id/actions', () => HttpResponse.error()),
      http.get('*/v1/runs/:id/artifacts', () => HttpResponse.error()),
    );
    const { user } = renderApp('/runs/run_demo?tab=actions', sessionFor('evaluator'));
    expect(await screen.findByText(/Could not load actions/)).toBeInTheDocument();
    await user.click(screen.getByRole('link', { name: 'Artifacts' }));
    expect(await screen.findByText(/Could not load artifacts/)).toBeInTheDocument();
  });

  it('evaluator: no controls; a replay run is badged and not offered for evaluation', async () => {
    server.use(
      http.get('*/v1/runs/:id', () =>
        HttpResponse.json({
          id: 'run_x',
          status: 'PASSED',
          epoch: 1,
          virtual_time: null,
          agent_actions: 0,
          replay_of: 'run_demo',
          scenario_key: null,
        }),
      ),
    );
    renderApp('/runs/run_x?tab=bogus', sessionFor('evaluator'));
    expect(await screen.findByText(/require a controller key/)).toBeInTheDocument();
    expect(screen.getByTestId('replay-badge')).toHaveTextContent('Replay of run_demo');
    expect(screen.getByTestId('replay-notice')).toHaveTextContent('Replays are not evaluated');
    expect(within(screen.getByTestId('replay-notice')).getByRole('link', { name: 'run_demo' })).toHaveAttribute('href', '/runs/run_demo');
    expect(screen.queryByRole('link', { name: 'Evaluations' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Evaluate and read results' })).toBeNull();
    expect(screen.getAllByRole('link', { name: 'run_demo' }).length).toBeGreaterThan(0);
    expect(screen.queryByRole('heading', { name: 'Run controls' })).toBeNull();
  });

  it('a run marked kind "replay" without replay_of, and a normal run, for an evaluator', async () => {
    server.use(
      http.get('*/v1/runs/:id', ({ params }) =>
        HttpResponse.json({
          id: String(params.id),
          status: 'PASSED',
          epoch: 1,
          virtual_time: null,
          agent_actions: 0,
          replay_of: null,
          ...(params.id === 'run_k' ? { kind: 'replay' } : {}),
        }),
      ),
    );
    const { unmount } = renderApp('/runs/run_k', sessionFor('evaluator'));
    expect(await screen.findByTestId('replay-badge')).toHaveTextContent(/^Replay$/);
    expect(screen.getByTestId('replay-notice')).toHaveTextContent(/^This run is a replay: /);
    unmount();
    renderApp('/runs/run_n', sessionFor('evaluator'));
    expect(await screen.findByRole('link', { name: 'Evaluations' })).toHaveAttribute('href', '/runs/run_n/evaluations');
    expect(screen.queryByTestId('replay-badge')).toBeNull();
  });

  it('UI-7: an action that produced no events shows "none", never a backwards range', async () => {
    const demo = mockDb.runs.run_demo!;
    demo.actions.push({ id: 9, actor: 'controller', operation: 'start', input: {}, result: {}, outcome: 'CONFLICT', virtual_time: null, seq_start: 18, seq_end: 17 });
    demo.actions.push({ id: 10, actor: 'controller', operation: 'noop', input: {}, result: {}, outcome: 'ok', virtual_time: null, seq_start: null, seq_end: null });
    renderApp('/runs/run_demo?tab=actions', sessionFor('evaluator'));
    await screen.findByTestId('actions-table');
    const conflict = rows('actions-table')[4] as HTMLElement;
    expect(conflict).toHaveTextContent('CONFLICT');
    expect(conflict).toHaveTextContent('none');
    expect(conflict).not.toHaveTextContent('#18');
    expect(rows('actions-table')[5]).toHaveTextContent('noopok——');
  });

  it('run errors: 500 and 403 for agents', async () => {
    renderApp('/runs/run_boom', sessionFor('controller'));
    expect(await screen.findByTestId('api-error')).toHaveTextContent('INTERNAL (HTTP 500)');
  });

  it('legacy actions without ids: refs are shown but nothing is highlighted', async () => {
    server.use(
      http.get('*/v1/runs/:id/actions', () =>
        HttpResponse.json([{ actor: 'agent', operation: 'reply', input: {}, result: {}, outcome: 'ok' }]),
      ),
    );
    renderApp('/runs/run_demo?tab=actions&action=3', sessionFor('evaluator'));
    expect(await screen.findByTestId('unmatched-action')).toHaveTextContent('3');
    expect(screen.queryByTestId('highlighted-action')).toBeNull();
    expect(rows('actions-table')[0]).toHaveTextContent('1agentreplyok——');
  });

  it('agent keys never reach the run page', async () => {
    renderApp('/runs/run_demo', sessionFor('agent'));
    expect(await screen.findByTestId('agent-key-notice')).toBeInTheDocument();
  });
});
