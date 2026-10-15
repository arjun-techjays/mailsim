import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../test/server';
import { renderApp, sessionFor } from '../test/utils';
import { validateInject } from './injectValidation';

async function openRun(path = '/runs/run_demo') {
  const r = renderApp(path, sessionFor('controller'));
  await screen.findByRole('heading', { name: 'Run controls' });
  await screen.findByRole('form', { name: 'Inject message' });
  return r;
}

describe('Run controls', () => {
  it('advance, tick, reset, replay, start conflict, cancel, inactive run', async () => {
    const { user } = await openRun();
    const result = () => screen.getByTestId('control-result');

    await user.clear(screen.getByLabelText('Advance clock by (seconds)'));
    await user.type(screen.getByLabelText('Advance clock by (seconds)'), '600');
    await user.click(screen.getByRole('button', { name: 'Advance clock' }));
    await waitFor(() => expect(result()).toHaveTextContent('fired evt_vendor_followup'));
    await waitFor(() => expect(screen.getByTestId('run-virtual-time')).toHaveTextContent('2026-03-02T09:12:00Z'));

    await user.click(screen.getByRole('button', { name: 'Scheduler tick' }));
    await waitFor(() => expect(result()).toHaveTextContent('Scheduler tick: virtual time is now'));
    expect(result()).toHaveTextContent('no scheduled events fired');

    await user.click(screen.getByRole('button', { name: 'Reset (new epoch)' }));
    await waitFor(() => expect(result()).toHaveTextContent('Reset: run is now RUNNING (epoch 3)'));

    await user.click(screen.getByRole('button', { name: 'Replay & compare hash' }));
    const replay = await screen.findByTestId('replay-result');
    expect(replay).toHaveTextContent('Replay matches');
    expect(replay).toHaveTextContent('identical');
    expect(replay).toHaveTextContent('none');
    expect(within(replay).getByRole('link', { name: 'run_demo_replay' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Start' }));
    expect(await screen.findByTestId('api-error')).toHaveTextContent('Operation failed: CONFLICT (HTTP 409)');

    await user.click(screen.getByRole('button', { name: 'Cancel run' }));
    await waitFor(() => expect(result()).toHaveTextContent('Cancelled: run is now CANCELLED'));

    await user.click(screen.getByRole('button', { name: 'Scheduler tick' }));
    expect(await screen.findByTestId('api-error')).toHaveTextContent('RUN_NOT_ACTIVE');
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
    await user.selectOptions(within(form).getByLabelText('Deliver to mailbox'), 'mbx_vendor');
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
