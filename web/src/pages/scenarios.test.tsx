import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../test/server';
import { renderApp, sessionFor } from '../test/utils';

describe('Scenarios', () => {
  it('lists scenarios and applies filters via the URL', async () => {
    const { user, router } = renderApp('/scenarios', sessionFor('controller'));
    const table = await screen.findByRole('table');
    expect(within(table).getAllByRole('row')).toHaveLength(3);
    expect(within(table).getByRole('link', { name: 'finance.invoice-bank-change' })).toBeInTheDocument();
    expect(within(table).getByText('v3')).toBeInTheDocument();

    await user.type(screen.getByLabelText('Tag'), 'support');
    await user.type(screen.getByLabelText('Search'), 'refund');
    await user.click(screen.getByRole('button', { name: 'Apply filters' }));
    expect(router.state.location.search).toBe('?tag=support&q=refund');
    expect(await screen.findByText('1 scenario(s)')).toBeInTheDocument();

    await user.clear(screen.getByLabelText('Tag'));
    await user.clear(screen.getByLabelText('Search'));
    await user.type(screen.getByLabelText('Search'), 'zzz');
    await user.click(screen.getByRole('button', { name: 'Apply filters' }));
    expect(await screen.findByTestId('empty-state')).toHaveTextContent('No scenarios match these filters.');

    await user.clear(screen.getByLabelText('Search'));
    await user.click(screen.getByRole('button', { name: 'Apply filters' }));
    expect(router.state.location.search).toBe('');
  });

  it('shows the empty project state', async () => {
    renderApp('/scenarios', sessionFor('controllerEmpty'));
    expect(await screen.findByTestId('empty-state')).toHaveTextContent('No scenarios have been published in this project yet.');
  });

  it('shows a 500 error envelope', async () => {
    renderApp('/scenarios?q=boom', sessionFor('controller'));
    const err = await screen.findByTestId('api-error');
    expect(err).toHaveTextContent('INTERNAL (HTTP 500)');
    expect(within(err).getByTestId('request-id')).toBeInTheDocument();
  });

  it('agent keys see the agent-key notice and no request is made', async () => {
    const seen: string[] = [];
    server.events.on('request:start', ({ request }) => {
      seen.push(request.url);
    });
    renderApp('/scenarios', sessionFor('agent'));
    expect(await screen.findByTestId('agent-key-notice')).toBeInTheDocument();
    expect(seen).toEqual([]);
    server.events.removeAllListeners();
  });

  it('scenario API 403 is shown with a role hint', async () => {
    server.use(
      http.get('*/v1/scenarios', () =>
        HttpResponse.json({ error: { code: 'PERMISSION_DENIED', message: 'no', details: {}, request_id: 'r' } }, { status: 403 }),
      ),
    );
    renderApp('/scenarios', sessionFor('controller'));
    expect(await screen.findByTestId('api-error')).toHaveTextContent('PERMISSION_DENIED (HTTP 403)');
  });

  it('renders scenarios without a latest version', async () => {
    server.use(http.get('*/v1/scenarios', () => HttpResponse.json([{ id: 's', key: 'draft.only', latest_version: null, versions: [] }])));
    renderApp('/scenarios', sessionFor('controller'));
    const row = (await screen.findByRole('link', { name: 'draft.only' })).closest('tr') as HTMLElement;
    expect(within(row).getAllByText('—')).toHaveLength(2);
  });
});

describe('Scenario detail', () => {
  it('controller sees versions and start link, but no answer keys', async () => {
    renderApp('/scenarios/finance.invoice-bank-change', sessionFor('controller'));
    expect(await screen.findByTestId('scenario-versions')).toHaveTextContent('v1v2v3');
    expect(screen.getByRole('link', { name: 'Start a run' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Answer-key versions' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Compare builds' })).toBeNull();
    expect(screen.getByText('sha256:spec3')).toBeInTheDocument();
  });

  it('evaluator sees answer-key metadata and compare link', async () => {
    renderApp('/scenarios/finance.invoice-bank-change', sessionFor('evaluator'));
    const keys = await screen.findByTestId('answer-keys');
    expect(within(keys).getAllByRole('row')).toHaveLength(3);
    expect(keys).toHaveTextContent('v1, v2');
    expect(keys).toHaveTextContent('all');
    expect(screen.getByRole('link', { name: 'Compare builds' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Start a run' })).toBeNull();
  });

  it('evaluator: empty and failing answer-key lists', async () => {
    server.use(http.get('*/v1/scenarios/:key/answer-keys', () => HttpResponse.json([])));
    renderApp('/scenarios/finance.invoice-bank-change', sessionFor('evaluator'));
    expect(await screen.findByText('No answer keys yet.')).toBeInTheDocument();
  });

  it('answer-key errors are shown', async () => {
    renderApp('/scenarios/support.refund-escalation', sessionFor('evaluator'));
    expect(await screen.findByText(/Could not load answer keys/)).toBeInTheDocument();
  });

  it('unknown scenario and missing latest version', async () => {
    renderApp('/scenarios/nope', sessionFor('controller'));
    expect(await screen.findByTestId('api-error')).toHaveTextContent('NOT_FOUND');
  });

  it('scenario without a published version', async () => {
    server.use(http.get('*/v1/scenarios/:key', () => HttpResponse.json({ id: 's', key: 'x', latest_version: null, versions: [] })));
    renderApp('/scenarios/x', sessionFor('controller'));
    expect(await screen.findByText('No published version.')).toBeInTheDocument();
  });
});
