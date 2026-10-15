import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { loadRecentRuns } from '../auth/storage';
import { server } from '../test/server';
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

  it('mailbox inspector: pagination, folders, message, thread, raw MIME link and mailbox switch', async () => {
    const { user, router } = renderApp('/runs/run_demo?tab=mailboxes', sessionFor('evaluator'));
    expect(await screen.findByText('20 message(s) loaded, more available')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Load more messages' }));
    expect(await screen.findByText('25 message(s) loaded')).toBeInTheDocument();

    const folders = screen.getByRole('navigation', { name: 'Folders' });
    await user.click(within(folders).getByRole('button', { name: 'sent' }));
    expect(await screen.findByText('1 message(s) loaded')).toBeInTheDocument();
    expect(within(folders).getByRole('button', { name: 'sent' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(within(folders).getByRole('button', { name: 'archive' }));
    expect(await screen.findByText('No messages in this folder.')).toBeInTheDocument();
    await user.click(within(folders).getByRole('button', { name: 'All folders' }));

    await user.click((await screen.findAllByRole('button', { name: 'Invoice 4471' }))[0] as HTMLElement);
    const view = await screen.findByTestId('message-view');
    expect(within(view).getByRole('heading', { level: 3 })).toHaveTextContent('Invoice 4471');
    expect(within(view).getByTestId('attachments')).toHaveTextContent('invoice-4471.pdf');
    expect(within(view).getByTestId('attachments')).toHaveTextContent('47.1 KB');
    expect(router.state.location.search).toContain('message=msg_invoice_1');
    const thread = await within(view).findByTestId('thread');
    expect(within(thread).getAllByRole('listitem')).toHaveLength(3);
    expect(within(view).getByText('All headers')).toBeInTheDocument();

    await user.click(within(thread).getAllByRole('button')[0] as HTMLElement);
    await waitFor(() => expect(router.state.location.search).toContain('message=msg_html_1'));
    const view2 = await screen.findByTestId('message-view');
    await waitFor(() => expect(within(view2).getByRole('heading', { level: 3 })).toHaveTextContent('updated bank details'));
    expect(within(view2).getByText('In-Reply-To')).toBeInTheDocument();
    expect(within(view2).getByText('IMPORTANT')).toBeInTheDocument();
    expect(document.querySelectorAll('script')).toHaveLength(0);

    await user.click(within(view2).getByTestId('raw-mime-link'));
    expect(router.state.location.search).toContain('tab=artifacts');
    expect(await screen.findByTestId('highlighted-artifact')).toHaveTextContent('blob/raw/msg_html_1.eml');

    await user.click(screen.getByRole('link', { name: 'Mailboxes' }));
    await user.selectOptions(await screen.findByLabelText('Mailbox'), 'mbx_vendor');
    expect(await screen.findByText('1 message(s) loaded')).toBeInTheDocument();
    expect(router.state.location.search).toContain('mailbox=mbx_vendor');
  });

  it('message deep link picks the owning mailbox; unknown ids show an error', async () => {
    renderApp('/runs/run_demo?tab=mailboxes&message=msg_vendor_in_1', sessionFor('evaluator'));
    expect(await screen.findByLabelText('Mailbox')).toHaveValue('mbx_vendor');
    expect(await screen.findByTestId('message-view')).toHaveTextContent('verify the change by phone');
  });

  it('unknown message id', async () => {
    renderApp('/runs/run_demo?tab=mailboxes&message=msg_nope', sessionFor('evaluator'));
    expect(await screen.findByText(/Could not load message/)).toBeInTheDocument();
  });

  it('state errors and empty mailbox lists', async () => {
    server.use(http.get('*/v1/runs/:id/state', () => HttpResponse.json({ mailboxes: [] })));
    renderApp('/runs/run_demo?tab=mailboxes', sessionFor('evaluator'));
    expect(await screen.findByText('This run has no mailboxes.')).toBeInTheDocument();
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

  it('event trace: cursor pagination, filters and highlight', async () => {
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
    expect(rows('event-table')).toHaveLength(24);
    await user.selectOptions(screen.getByLabelText('Epoch'), '2');
    expect(rows('event-table')).toHaveLength(4);
    await user.selectOptions(screen.getByLabelText('Type'), 'message.read');
    expect(screen.getByTestId('empty-state')).toHaveTextContent('No events match.');
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

  it('actions table with evidence highlight, and artifacts', async () => {
    const { user } = renderApp('/runs/run_demo?tab=actions&action=3', sessionFor('evaluator'));
    expect(await screen.findByTestId('actions-table')).toBeInTheDocument();
    expect(rows('actions-table')).toHaveLength(4);
    expect(rows('actions-table')[2]).toHaveClass('highlight');
    expect(screen.getByText(/matched by position/)).toBeInTheDocument();
    await user.click(screen.getByRole('link', { name: 'Artifacts' }));
    expect(await screen.findByTestId('artifacts-table')).toHaveTextContent('trace/run_demo.jsonl');
    expect(screen.getByTestId('artifacts-table')).toHaveTextContent('unknown');
  });

  it('empty actions/artifacts and their errors', async () => {
    server.use(
      http.get('*/v1/runs/:id/actions', () => HttpResponse.json([])),
      http.get('*/v1/runs/:id/artifacts', () => HttpResponse.json([])),
    );
    const { user } = renderApp('/runs/run_demo?tab=actions', sessionFor('evaluator'));
    expect(await screen.findByText('No actions recorded.')).toBeInTheDocument();
    await user.click(screen.getByRole('link', { name: 'Artifacts' }));
    expect(await screen.findByText('No artifacts.')).toBeInTheDocument();
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

  it('evaluator: no controls, evaluations link; replay_of link and unknown tab', async () => {
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
    expect(screen.getByRole('link', { name: 'Evaluations' })).toHaveAttribute('href', '/runs/run_x/evaluations');
    expect(screen.getByRole('link', { name: 'run_demo' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Run controls' })).toBeNull();
  });

  it('run errors: 500 and 403 for agents', async () => {
    renderApp('/runs/run_boom', sessionFor('controller'));
    expect(await screen.findByTestId('api-error')).toHaveTextContent('INTERNAL (HTTP 500)');
  });

  it('agent is refused with 403', async () => {
    renderApp('/runs/run_demo', sessionFor('agent'));
    expect(await screen.findByTestId('api-error')).toHaveTextContent('PERMISSION_DENIED (HTTP 403)');
  });
});
