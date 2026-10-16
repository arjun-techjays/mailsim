import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { loadRecentRuns } from '../auth/storage';
import { mockDb, server } from '../test/server';
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

  describe('slow create (UI-8)', () => {
    afterEach(() => {
      vi.unstubAllEnvs();
      server.events.removeAllListeners();
    });

    it('keeps one Idempotency-Key across retries, recovers the run and offers a new agent key', async () => {
      vi.stubEnv('VITE_LONG_REQUEST_TIMEOUT_MS', '300');
      const keys: (string | null)[] = [];
      server.events.on('request:start', ({ request }) => {
        if (request.method === 'POST' && request.url.endsWith('/runs')) keys.push(request.headers.get('idempotency-key'));
      });
      const before = Object.keys(mockDb.runs).length;
      const { user } = renderApp('/scenarios/finance.invoice-bank-change/start', sessionFor('controller'));
      await user.type(await screen.findByLabelText('Agent build id'), 'slow');
      await user.click(screen.getByRole('button', { name: 'Create run' }));
      expect(await screen.findByText(/seeding its mailboxes/)).toBeInTheDocument();
      const notice = await screen.findByTestId('unconfirmed');
      expect(notice).toHaveTextContent('The run was not confirmed');
      expect(notice).toHaveTextContent('never creates a second run');
      expect(within(notice).getByTestId('api-error')).toHaveTextContent('Request timed out');

      await user.click(within(notice).getByRole('button', { name: 'Check again' }));
      const recovered = await screen.findByTestId('run-recovered');
      expect(keys).toHaveLength(2);
      expect(keys[0]).toMatch(/^dash-/);
      expect(keys[1]).toBe(keys[0]);
      expect(Object.keys(mockDb.runs).length).toBe(before + 1); // one run, not two
      expect(recovered).toHaveTextContent('Run run_001 was created');
      expect(recovered).not.toHaveTextContent('redacted');
      expect(screen.queryByTestId('agent-credentials')).toBeNull();
      expect(within(recovered).getByRole('link', { name: 'Connect your agent' })).toHaveAttribute('href', '/runs/run_001/connect');

      await user.click(within(recovered).getByRole('button', { name: 'Mint a new agent key' }));
      const panel = await screen.findByTestId('agent-credentials');
      expect((within(panel).getByTestId('agent-api-key') as HTMLInputElement).value).toMatch(/^msk_000000000001_/);
      expect(screen.queryByTestId('run-recovered')).toBeNull();

      // a new action gets a new key
      await user.click(within(panel).getByRole('button', { name: /hide it/ }));
      await user.clear(screen.getByLabelText('Agent build id'));
      await user.type(screen.getByLabelText('Agent build id'), 'fast');
      await user.click(screen.getByRole('button', { name: 'Create run' }));
      await screen.findByTestId('agent-credentials');
      expect(keys[2]).not.toBe(keys[0]);
    });

    it('a retry while the first attempt still runs says so; minting can fail', async () => {
      let calls = 0;
      server.use(
        http.post('*/v1/scenarios/:key/runs', () => {
          calls += 1;
          if (calls === 1) return HttpResponse.error();
          if (calls === 2) {
            return HttpResponse.json(
              { error: { code: 'CONFLICT', message: 'a request with this Idempotency-Key is in progress', details: { idempotency_key: 'k' }, request_id: 'r' } },
              { status: 409 },
            );
          }
          return HttpResponse.json(
            {
              run: { id: 'run_r', status: 'RUNNING', epoch: 1, virtual_time: null, agent_actions: 0, replay_of: null },
              agent_credentials: [{ api_key: '<redacted: shown once>', mailbox_id: 'm', mailbox_address: 'a@x', run_id: 'run_r' }],
            },
            { status: 201 },
          );
        }),
        http.post('*/v1/runs/:id/agent-credentials', () =>
          HttpResponse.json({ error: { code: 'CONFLICT', message: 'run is CANCELLED', details: {}, request_id: 'r' } }, { status: 409 }),
        ),
      );
      const { user } = renderApp('/scenarios/finance.invoice-bank-change/start', sessionFor('controller'));
      await user.click(await screen.findByRole('button', { name: 'Create run' }));
      const notice = await screen.findByTestId('unconfirmed');
      expect(within(notice).getByTestId('api-error')).toHaveTextContent('Network error');
      await user.click(within(notice).getByRole('button', { name: 'Check again' }));
      await waitFor(() => expect(screen.getByTestId('unconfirmed')).toHaveTextContent('The server is still creating the run'));
      expect(within(screen.getByTestId('unconfirmed')).queryByTestId('api-error')).toBeNull();
      await user.click(screen.getByRole('button', { name: 'Check again' }));
      const recovered = await screen.findByTestId('run-recovered');
      await user.click(within(recovered).getByRole('button', { name: 'Mint a new agent key' }));
      expect(await within(recovered).findByTestId('api-error')).toHaveTextContent('Could not mint an agent key: CONFLICT (HTTP 409)');
    });

    it('shows progress while minting, and copes with a response without credentials', async () => {
      server.use(
        http.post('*/v1/scenarios/:key/runs', () =>
          HttpResponse.json(
            {
              run: { id: 'run_r', status: 'RUNNING', epoch: 1, virtual_time: null, agent_actions: 0, replay_of: null },
              agent_credentials: [{ api_key: '<redacted: shown once>', mailbox_id: 'm', mailbox_address: 'a@x', run_id: 'run_r' }],
            },
            { status: 201 },
          ),
        ),
        http.post('*/v1/runs/:id/agent-credentials', async () => {
          await new Promise((r) => setTimeout(r, 200));
          return HttpResponse.json({ api_key: 'msk_000000000009_x', mailbox_id: 'm', mailbox_address: 'a@x', run_id: 'run_r' }, { status: 201 });
        }),
      );
      const { user } = renderApp('/scenarios/finance.invoice-bank-change/start', sessionFor('controller'));
      await user.click(await screen.findByRole('button', { name: 'Create run' }));
      await user.click(await screen.findByRole('button', { name: 'Mint a new agent key' }));
      expect(await screen.findByRole('button', { name: 'Minting…' })).toBeDisabled();
      expect(await screen.findByTestId('agent-credentials')).toHaveTextContent('a@x');

      server.use(
        http.post('*/v1/scenarios/:key/runs', () =>
          HttpResponse.json(
            { run: { id: 'run_n', status: 'READY', epoch: 1, virtual_time: null, agent_actions: 0, replay_of: null }, agent_credentials: [] },
            { status: 201 },
          ),
        ),
      );
      await user.click(screen.getByRole('button', { name: 'Create run' }));
      expect(await screen.findByRole('link', { name: 'Go to run run_n' })).toBeInTheDocument();
    });

    it('other errors are shown as before (no recovery offer)', async () => {
      server.use(
        http.post('*/v1/scenarios/:key/runs', () =>
          HttpResponse.json({ error: { code: 'INVALID_REQUEST', message: 'bad', details: {}, request_id: 'r' } }, { status: 400 }),
        ),
      );
      const { user } = renderApp('/scenarios/finance.invoice-bank-change/start', sessionFor('controller'));
      await user.click(await screen.findByRole('button', { name: 'Create run' }));
      expect(await screen.findByTestId('api-error')).toHaveTextContent('Could not create run: INVALID_REQUEST (HTTP 400)');
      expect(screen.queryByTestId('unconfirmed')).toBeNull();
    });
  });
});
