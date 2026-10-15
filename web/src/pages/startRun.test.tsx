import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { loadRecentRuns } from '../auth/storage';
import { server } from '../test/server';
import { renderApp, sessionFor } from '../test/utils';

describe('Start run', () => {
  it('creates a run and shows the agent credential once', async () => {
    let body: unknown = null;
    server.events.on('request:start', async ({ request }) => {
      if (request.method === 'POST' && request.url.endsWith('/runs')) body = await request.clone().json();
    });
    const { user, router } = renderApp('/scenarios/finance.invoice-bank-change/start', sessionFor('controller'));
    await user.selectOptions(await screen.findByLabelText('Scenario version'), '2');
    await user.click(screen.getByRole('radio', { name: 'On' }));
    await user.type(screen.getByLabelText('Agent build id'), ' agent-v7 ');
    await user.click(screen.getByLabelText('Start the run immediately'));
    await user.click(screen.getByRole('button', { name: 'Create run' }));

    const panel = await screen.findByTestId('agent-credentials');
    expect(body).toEqual({ scenario_version: 2, checks: 'on', agent_build: 'agent-v7', start: false });
    const key = within(panel).getByTestId('agent-api-key') as HTMLInputElement;
    expect(key.value).toBe('mk_agent_run_001_finance');
    expect(within(panel).getByRole('alert')).toHaveTextContent('will not be shown again');
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(key.value);
    expect(loadRecentRuns()[0]).toEqual({ id: 'run_001', scenarioKey: 'finance.invoice-bank-change' });
    key.focus();

    await user.click(within(panel).getByRole('button', { name: /hide it/ }));
    expect(screen.queryByTestId('agent-credentials')).toBeNull();
    expect(screen.queryByDisplayValue('mk_agent_run_001_finance')).toBeNull();
    await user.click(screen.getByRole('link', { name: 'Open run run_001' }));
    expect(router.state.location.pathname).toBe('/runs/run_001');
    expect(await screen.findByTestId('run-summary')).toHaveTextContent('READY');
    server.events.removeAllListeners();
  });

  it('defaults: latest version, answer-key checks default, no build', async () => {
    let body: unknown = null;
    server.events.on('request:start', async ({ request }) => {
      if (request.method === 'POST') body = await request.clone().json();
    });
    const { user } = renderApp('/scenarios/finance.invoice-bank-change/start', sessionFor('controller'));
    await user.click(await screen.findByRole('radio', { name: 'Off' }));
    await user.click(screen.getByRole('radio', { name: 'Answer-key default' }));
    await user.click(screen.getByRole('button', { name: 'Create run' }));
    await screen.findByTestId('agent-credentials');
    expect(body).toEqual({ scenario_version: null, checks: null, agent_build: null, start: true });
    await user.click(screen.getByRole('link', { name: /Go to run/ }));
    expect(await screen.findByTestId('run-summary')).toHaveTextContent('RUNNING');
    server.events.removeAllListeners();
  });

  it('shows creation errors and scenario errors', async () => {
    server.use(
      http.post('*/v1/scenarios/:key/runs', () =>
        HttpResponse.json({ error: { code: 'CONFLICT', message: 'busy', details: {}, request_id: 'r9' } }, { status: 409 }),
      ),
    );
    const { user } = renderApp('/scenarios/finance.invoice-bank-change/start', sessionFor('controller'));
    await user.click(await screen.findByRole('button', { name: 'Create run' }));
    expect(await screen.findByTestId('api-error')).toHaveTextContent('Could not create run: CONFLICT (HTTP 409)');
  });

  it('unknown scenario', async () => {
    renderApp('/scenarios/nope/start', sessionFor('controller'));
    expect(await screen.findByTestId('api-error')).toHaveTextContent('Could not load scenario');
  });

  it('scenario without latest version shows a placeholder option', async () => {
    server.use(http.get('*/v1/scenarios/:key', () => HttpResponse.json({ id: 's', key: 'x', latest_version: null, versions: [] })));
    renderApp('/scenarios/x/start', sessionFor('controller'));
    expect(await screen.findByRole('option', { name: 'Latest (v?)' })).toBeInTheDocument();
  });
});
