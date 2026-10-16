import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { mockDb, server } from '../test/server';
import { renderApp, sessionFor } from '../test/utils';
import { validateInject } from './injectValidation';

async function openRun(path = '/runs/run_demo') {
  const r = renderApp(path, sessionFor('controller'));
  await screen.findByRole('heading', { name: 'Run controls' });
  await screen.findByRole('form', { name: 'Inject message' });
  return r;
}

describe('Run controls', () => {
  it('advance, tick, confirmed reset, replay, confirmed cancel; actions follow the run state (UI-6)', async () => {
    const { user } = await openRun();
    const result = () => screen.getByTestId('control-result');
    const btn = (name: string) => screen.getByRole('button', { name });

    // RUNNING: start is not offered, everything else is
    expect(screen.getByTestId('controls-state')).toHaveTextContent('This run is RUNNING');
    expect(btn('Start')).toBeDisabled();
    for (const name of ['Cancel run', 'Reset (new epoch)', 'Replay & compare hash', 'Scheduler tick', 'Advance clock']) {
      expect(btn(name)).toBeEnabled();
    }

    await user.clear(screen.getByLabelText('Advance clock by (seconds)'));
    await user.type(screen.getByLabelText('Advance clock by (seconds)'), '600');
    await user.click(btn('Advance clock'));
    await waitFor(() => expect(result()).toHaveTextContent('fired evt_vendor_followup'));
    expect(screen.getByTestId('persona-replies')).toHaveTextContent('1 persona reply delivered');
    await waitFor(() => expect(screen.getByTestId('run-virtual-time')).toHaveTextContent('2026-03-02T09:12:00Z'));

    await user.click(btn('Scheduler tick'));
    await waitFor(() => expect(result()).toHaveTextContent('Scheduler tick: virtual time is now'));
    expect(result()).toHaveTextContent('no scheduled events fired');
    expect(screen.getByTestId('persona-replies')).toHaveTextContent('0 persona replies delivered');

    // reset asks first; Escape keeps the run and returns focus
    await user.click(btn('Reset (new epoch)'));
    const resetDialog = screen.getByRole('alertdialog', { name: 'Reset this run to its starting state?' });
    expect(resetDialog).toHaveTextContent('new epoch (epoch 3)');
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(btn('Reset (new epoch)')).toHaveFocus();
    expect(screen.getByTestId('run-epoch')).toHaveTextContent('2');
    await user.click(btn('Reset (new epoch)'));
    expect(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Keep the current state' })).toHaveFocus();
    await user.click(btn('Reset run'));
    expect(result()).toHaveFocus(); // the dialog is gone: focus continues at the outcome
    await waitFor(() => expect(result()).toHaveTextContent('Reset: run is now RUNNING (epoch 3)'));

    await user.click(btn('Replay & compare hash'));
    const replay = await screen.findByTestId('replay-result');
    expect(replay).toHaveTextContent('Replay matches');
    expect(replay).toHaveTextContent('identical');
    expect(replay).toHaveTextContent('none');
    expect(within(replay).getByRole('link', { name: 'run_demo_replay' })).toBeInTheDocument();

    // a failing call shows its error until the run's state changes
    server.use(http.post('*/v1/runs/:id/scheduler/tick', () => HttpResponse.json({ error: { code: 'INTERNAL', message: 'x', details: {}, request_id: 'r' } }, { status: 500 })));
    await user.click(btn('Scheduler tick'));
    expect(await screen.findByTestId('api-error')).toHaveTextContent('Operation failed: INTERNAL (HTTP 500)');

    // cancel asks first (and can be called off), then the run is terminal
    await user.click(btn('Cancel run'));
    const cancelDialog = screen.getByRole('alertdialog', { name: 'Cancel this run?' });
    await user.click(within(cancelDialog).getByRole('button', { name: 'Keep running' }));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    await user.click(btn('Cancel run'));
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Cancel run' }));
    expect(result()).toHaveFocus();
    await waitFor(() => expect(result()).toHaveTextContent('Cancelled: run is now CANCELLED'));
    await waitFor(() => expect(screen.queryByTestId('api-error')).toBeNull()); // cleared by the state change
    expect(screen.getByTestId('controls-state')).toHaveTextContent('This run is CANCELLED and cannot change any more');
    for (const name of ['Start', 'Cancel run', 'Reset (new epoch)', 'Scheduler tick', 'Advance clock']) {
      expect(btn(name)).toBeDisabled();
    }
    expect(btn('Replay & compare hash')).toBeEnabled();
    expect(screen.getByTestId('inject-inactive')).toHaveTextContent('Injecting mail needs a RUNNING run; this run is CANCELLED.');
    expect(within(screen.getByRole('form', { name: 'Inject message' })).getByRole('button', { name: 'Inject message' })).toBeDisabled();
  });

  it('a start rejected because the run changed elsewhere explains itself and refreshes (no raw 409)', async () => {
    const { user } = renderApp('/scenarios/finance.invoice-bank-change/start', sessionFor('controller'));
    await user.click(await screen.findByLabelText('Start the run immediately'));
    await user.click(screen.getByRole('button', { name: 'Create run' }));
    await user.click(await screen.findByRole('link', { name: /Go to run/ }));
    await screen.findByRole('heading', { name: 'Run controls' });
    expect(screen.getByTestId('controls-state')).toHaveTextContent('This run is READY');
    expect(screen.getByRole('button', { name: 'Advance clock' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel run' })).toBeEnabled();
    const id = Object.keys(mockDb.runs).find((k) => mockDb.runs[k]?.run.status === 'READY' && k !== 'run_empty') as string;
    (mockDb.runs[id] as { run: { status: string } }).run.status = 'RUNNING'; // started by someone else
    await user.click(screen.getByRole('button', { name: 'Start' }));
    expect(await screen.findByTestId('control-note')).toHaveTextContent(/Could not start the run: cannot start a RUNNING run\. The run’s state was refreshed/);
    expect(screen.queryByTestId('api-error')).toBeNull();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start' })).toBeDisabled());
    expect(screen.getByTestId('controls-state')).toHaveTextContent('This run is RUNNING');
  });

  it('describes states without controls (DRAFT)', async () => {
    server.use(
      http.get('*/v1/runs/:id', () =>
        HttpResponse.json({ id: 'run_d', status: 'DRAFT', epoch: 1, virtual_time: null, agent_actions: 0, replay_of: null }),
      ),
    );
    renderApp('/runs/run_d', sessionFor('controller'));
    expect(await screen.findByTestId('controls-state')).toHaveTextContent('This run is DRAFT: it is not ready yet, so only replay is available.');
    expect(screen.getByRole('button', { name: 'Start' })).toBeDisabled();
  });

  it('starts a READY run and shows a non-matching replay', async () => {
    server.use(
      http.post('*/v1/runs/:id/replay', () =>
        HttpResponse.json({
          replay_run_id: 'r2',
          matches: false,
          original_hash: 'a',
          replay_hash: 'b',
          event_count: 3,
          missing_blobs: ['blob/x'],
        }),
      ),
    );
    const { user } = renderApp('/scenarios/finance.invoice-bank-change/start', sessionFor('controller'));
    await user.click(await screen.findByLabelText('Start the run immediately'));
    await user.click(screen.getByRole('button', { name: 'Create run' }));
    await user.click(await screen.findByRole('link', { name: /Go to run/ }));
    await screen.findByRole('heading', { name: 'Run controls' });
    await user.click(screen.getByRole('button', { name: 'Start' }));
    await waitFor(() => expect(screen.getByTestId('control-result')).toHaveTextContent('Started: run is now RUNNING'));
    await user.click(screen.getByRole('button', { name: 'Replay & compare hash' }));
    const replay = await screen.findByTestId('replay-result');
    expect(replay).toHaveTextContent('does NOT match');
    expect(replay).toHaveTextContent('differs');
    expect(replay).toHaveTextContent('blob/x');
  });

  it('treats a missing persona_replies as zero', async () => {
    server.use(http.post('*/v1/runs/:id/scheduler/tick', () => HttpResponse.json({ fired: [], virtual_time: 'T' })));
    const { user } = await openRun();
    await user.click(screen.getByRole('button', { name: 'Scheduler tick' }));
    expect(await screen.findByTestId('persona-replies')).toHaveTextContent('0 persona replies delivered');
  });

  it('validates the advance amount client-side', async () => {
    const { user } = await openRun();
    await user.clear(screen.getByLabelText('Advance clock by (seconds)'));
    await user.click(screen.getByRole('button', { name: 'Advance clock' }));
    expect(screen.getByText('Enter a number of seconds (0 or more).')).toBeInTheDocument();
    expect(screen.getByLabelText('Advance clock by (seconds)')).toHaveAttribute('aria-invalid', 'true');
  });

  it('shows a loading state for mailboxes and handles state failures on the overview', async () => {
    server.use(http.get('*/v1/runs/:id/state', () => HttpResponse.error()));
    renderApp('/runs/run_demo', sessionFor('controller'));
    expect(await screen.findByText(/Could not load mailboxes for injection/)).toBeInTheDocument();
  });
});

describe('Inject form', () => {
  it('validates required fields client-side with an error summary', async () => {
    const { user } = await openRun();
    const form = screen.getByRole('form', { name: 'Inject message' });
    await user.clear(within(form).getByLabelText('To (comma-separated)'));
    await user.click(within(form).getByRole('button', { name: 'Inject message' }));
    const summary = screen.getByTestId('inject-validation');
    expect(summary).toHaveTextContent('Fix 3 field(s)');
    expect(within(form).getByLabelText('From')).toHaveAttribute('aria-invalid', 'true');
    expect(within(form).getByLabelText('From')).toHaveAccessibleDescription('Sender address is required.');
    expect(within(summary).getByRole('link', { name: 'Text body' })).toHaveAttribute('href', '#inj-text');
  });

  it('shows server validation errors with field paths and request id', async () => {
    const { user } = await openRun();
    const form = screen.getByRole('form', { name: 'Inject message' });
    await user.type(within(form).getByLabelText('From'), 'not-an-email');
    await user.type(within(form).getByLabelText('Text body'), 'hi');
    await user.click(within(form).getByRole('button', { name: 'Inject message' }));
    const err = await screen.findByTestId('api-error');
    expect(err).toHaveTextContent('Injection rejected: INVALID_REQUEST (HTTP 400)');
    expect(within(err).getByRole('list', { name: 'Field errors' })).toHaveTextContent('message.from: value is not a valid email address');
    expect(within(err).getByTestId('request-id')).toHaveTextContent(/req_mock_/);
  });

  it('injects a message into the chosen mailbox and refreshes the run', async () => {
    let body: unknown = null;
    server.events.on('request:start', async ({ request }) => {
      if (request.url.includes('/inject')) body = await request.clone().json();
    });
    const { user } = await openRun();
    const form = screen.getByRole('form', { name: 'Inject message' });
    expect(within(form).getByLabelText('To (comma-separated)')).toHaveValue('finance@acme.test');
    await user.selectOptions(within(form).getByLabelText('Deliver to mailbox'), 'mbx_vendor');
    expect(within(form).getByLabelText('To (comma-separated)')).toHaveValue('billing@supplier.test');
    await user.clear(within(form).getByLabelText('To (comma-separated)'));
    await user.type(within(form).getByLabelText('To (comma-separated)'), 'billing@supplier.test');
    await user.type(within(form).getByLabelText('Cc (comma-separated)'), 'a@x.test, b@x.test');
    await user.type(within(form).getByLabelText('From'), 'ceo@acme.test');
    await user.type(within(form).getByLabelText('Subject'), 'Wire');
    await user.type(within(form).getByLabelText(/HTML body/), '<b>pay</b>');
    await user.type(within(form).getByLabelText('Event id (optional)'), 'evt_x');
    await user.click(within(form).getByRole('button', { name: 'Inject message' }));
    expect(await screen.findByText(/Delivered to simulated mailbox/)).toHaveTextContent('evt_x');
    expect(body).toEqual({
      event_id: 'evt_x',
      message: { from: 'ceo@acme.test', to: ['billing@supplier.test'], cc: ['a@x.test', 'b@x.test'], subject: 'Wire', text: null, html: '<b>pay</b>' },
      delivery: { mode: 'immediate' },
    });
    server.events.removeAllListeners();
  });

  it('falls back to a free-text mailbox id when the state lists none', async () => {
    server.use(http.get('*/v1/runs/:id/state', () => HttpResponse.json({})));
    const { user } = await openRun();
    const form = screen.getByRole('form', { name: 'Inject message' });
    const mailbox = within(form).getByLabelText('Deliver to mailbox');
    expect(mailbox.tagName).toBe('INPUT');
    await user.click(within(form).getByRole('button', { name: 'Inject message' }));
    expect(screen.getByTestId('inject-validation')).toHaveTextContent('Deliver to mailbox');
    expect(mailbox).toHaveAttribute('aria-invalid', 'true');
    await user.type(mailbox, 'mbx_finance');
    await user.type(within(form).getByLabelText('From'), 'x@y.test');
    await user.type(within(form).getByLabelText('To (comma-separated)'), 'finance@acme.test');
    await user.type(within(form).getByLabelText('Text body'), 'hello');
    await user.click(within(form).getByRole('button', { name: 'Inject message' }));
    expect(await screen.findByText(/Delivered to simulated mailbox/)).toBeInTheDocument();
  });

  it('validateInject accepts html-only bodies', () => {
    const base = { mailbox: 'm', from: 'a@b', to: 'c@d', cc: '', subject: '', text: '', html: '<p>x</p>', eventId: '' };
    expect(validateInject(base)).toEqual({});
  });
});
