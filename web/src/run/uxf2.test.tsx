import { QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { delay, http, HttpResponse } from 'msw';
import { MemoryRouter } from 'react-router-dom';
import type { RunOut } from '../api/types';
import { SessionProvider } from '../auth/SessionProvider';
import { makeQueryClient } from '../queryClient';
import { mockDb, server } from '../test/server';
import { renderApp, sessionFor } from '../test/utils';
import { interfaceLabel, outcomeKind, summarizeInput } from './activity';
import { ActivityPanel } from './ActivityPanel';

const fail = (status: number, code = 'INTERNAL') =>
  HttpResponse.json({ error: { code, message: 'nope', details: {}, request_id: 'r1' } }, { status });

describe('Run toolbar', () => {
  it('is shown for controllers outside the overview and reports what each step did', async () => {
    const { user } = renderApp('/runs/run_demo?tab=activity', sessionFor('controller'));
    const bar = await screen.findByTestId('run-toolbar');
    await user.click(within(bar).getByRole('button', { name: 'Advance the clock 10 minutes' }));
    const result = await within(bar).findByTestId('toolbar-result');
    expect(result).toHaveTextContent('Advanced the clock by 10 minutes.');
    expect(result).toHaveTextContent('2 Mar 2026, 09:02 UTC → 2 Mar 2026, 09:12 UTC');
    expect(result).toHaveTextContent('Scheduled events fired: evt_vendor_followup.');
    expect(result).toHaveTextContent('1 persona reply delivered.');
    expect(within(result).getByRole('link', { name: 'Following up: bank details' })).toHaveAttribute(
      'href',
      '/runs/run_demo?tab=mailboxes&message=msg_followup_1',
    );
    await waitFor(() => expect(screen.getByTestId('run-virtual-time')).toHaveTextContent('2026-03-02T09:12:00Z'));

    await user.click(within(bar).getByRole('button', { name: 'Tick scheduler' }));
    await waitFor(() => expect(within(bar).getByTestId('toolbar-result')).toHaveTextContent('Scheduler tick.'));
    expect(within(bar).getByTestId('toolbar-result')).toHaveTextContent('No scheduled events were due. 0 persona replies delivered.');
    expect(within(bar).getByTestId('toolbar-result')).toHaveTextContent('No new messages arrived.');

    await user.click(within(bar).getByRole('button', { name: 'Advance' }));
    expect(within(bar).getByText('Enter a number of seconds greater than 0.')).toBeInTheDocument();
    await user.type(within(bar).getByLabelText('Custom (seconds)'), '1');
    await user.click(within(bar).getByRole('button', { name: 'Advance' }));
    await waitFor(() => expect(within(bar).getByTestId('toolbar-result')).toHaveTextContent('Advanced the clock by 1 second.'));
    await user.clear(within(bar).getByLabelText('Custom (seconds)'));
    await user.type(within(bar).getByLabelText('Custom (seconds)'), '90');
    await user.click(within(bar).getByRole('button', { name: 'Advance' }));
    await waitFor(() => expect(within(bar).getByTestId('toolbar-result')).toHaveTextContent('Advanced the clock by 90 seconds.'));
  });

  it('is hidden on the overview and for evaluators; inactive runs and failures explain themselves', async () => {
    const a = renderApp('/runs/run_demo', sessionFor('controller'));
    await screen.findByRole('heading', { name: 'Run controls' });
    expect(screen.queryByTestId('run-toolbar')).toBeNull();
    a.unmount();
    const b = renderApp('/runs/run_demo?tab=trace', sessionFor('evaluator'));
    await screen.findByTestId('run-summary');
    expect(screen.queryByTestId('run-toolbar')).toBeNull();
    b.unmount();

    const c = renderApp('/runs/run_empty?tab=trace', sessionFor('controller'));
    const bar = await screen.findByTestId('run-toolbar');
    expect(within(bar).getByTestId('toolbar-inactive')).toHaveTextContent('this run is READY');
    expect(within(bar).getByRole('button', { name: 'Tick scheduler' })).toBeDisabled();
    c.unmount();

    // state unavailable: the result still reports the clock; tick fails with an error envelope
    server.use(
      http.get('*/v1/runs/:id/state', () => fail(500)),
      http.post('*/v1/runs/:id/clock/advance', () => HttpResponse.json({ fired: [], virtual_time: '2026-03-02T09:03:00Z' })),
      http.post('*/v1/runs/:id/scheduler/tick', () => fail(409, 'RUN_NOT_ACTIVE')),
    );
    const { user } = renderApp('/runs/run_demo?tab=trace', sessionFor('controller'));
    const bar2 = await screen.findByTestId('run-toolbar');
    await user.click(within(bar2).getByRole('button', { name: 'Advance the clock 1 minute' }));
    await waitFor(() => expect(within(bar2).getByTestId('toolbar-result')).toHaveTextContent('0 persona replies delivered.'));
    expect(within(bar2).queryByText('No new messages arrived.')).toBeNull();
    await user.click(within(bar2).getByRole('button', { name: 'Tick scheduler' }));
    expect(await within(bar2).findByText(/Clock operation failed/)).toBeInTheDocument();
  });

  it('virtual time without a previous value', async () => {
    mockDb.runs.run_demo!.run.virtual_time = null;
    const { user } = renderApp('/runs/run_demo?tab=trace', sessionFor('controller'));
    const bar = await screen.findByTestId('run-toolbar');
    await user.click(within(bar).getByRole('button', { name: 'Advance the clock 1 day' }));
    await waitFor(() => expect(within(bar).getByTestId('toolbar-result')).toHaveTextContent('Virtual time is now'));
  });
});

function renderPanel(run: RunOut, props: { pollMs?: number; announceMs?: number } = {}) {
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={makeQueryClient()}>
      <SessionProvider initial={sessionFor('evaluator')} probe={false}>
        <MemoryRouter>
          <ActivityPanel run={run} {...props} />
        </MemoryRouter>
      </SessionProvider>
    </QueryClientProvider>,
  );
  return user;
}

const renderPanelWithUnmount = renderPanel;
const demo = () => mockDb.runs.run_demo!;
const push = (id: number, operation: string, extra: Partial<{ interface: string | null; actor: string; outcome: string }> = {}) =>
  demo().actions.push({
    id,
    actor: extra.actor ?? 'agent',
    operation,
    input: { message_id: 'm'.repeat(50) },
    result: {},
    outcome: extra.outcome ?? 'ok',
    virtual_time: '2026-03-02T09:05:00Z',
    interface: extra.interface,
  });

describe('Activity panel', () => {
  it('lists actions newest first with interface badges and filters by actor (run page)', async () => {
    const { user } = renderApp('/runs/run_demo?tab=activity', sessionFor('evaluator'));
    const table = await screen.findByTestId('activity-table');
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveTextContent('modify_labels');
    expect(rows[0]).toHaveTextContent('IMAP');
    expect(rows[0]).toHaveTextContent('denied');
    expect(rows[3]).toHaveTextContent('REST API');
    expect(rows[3]).toHaveTextContent('folder=inbox');
    expect(within(table).getAllByTestId('interface-badge').map((b) => b.textContent)).toEqual(['IMAP', 'Gmail API', 'MCP (HTTP)', 'REST API']);
    expect(screen.getByTestId('activity-status')).toHaveTextContent('Live: checking for new activity every 3 s.');
    await user.selectOptions(screen.getByLabelText('Show'), 'agent');
    expect(within(screen.getByTestId('activity-table')).getAllByRole('row')).toHaveLength(5);
  });

  it('polls while running, announces new rows politely (rate-limited), pauses and resumes', async () => {
    const user = renderPanel(demo().run, { pollMs: 40, announceMs: 150 });
    await screen.findByTestId('activity-table');
    push(5, 'forward', { interface: 'smtp' });
    await waitFor(() => expect(screen.getByText('forward', { selector: 'th' })).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByTestId('activity-announcer')).toHaveTextContent('1 new action. Latest: agent forward (ok) via SMTP.'),
    );
    // a second batch inside the window is held back, then announced together
    push(6, 'send', { interface: 'mcp_stdio', actor: 'simulator' });
    push(7, 'read', { interface: null, outcome: 'NOT_FOUND' });
    await waitFor(() => expect(screen.getAllByTestId('interface-badge')[0]).toHaveTextContent('Unknown'));
    await waitFor(() => expect(screen.getByTestId('activity-announcer')).toHaveTextContent('2 new actions. Latest: agent read (NOT_FOUND) via Unknown.'));
    expect(screen.getByText('NOT_FOUND')).toHaveClass('badge-error-code');

    await user.click(screen.getByRole('button', { name: 'Pause live updates' }));
    expect(screen.getByTestId('activity-status')).toHaveTextContent('Paused. Resume to keep updating.');
    push(8, 'move');
    await new Promise((r) => setTimeout(r, 150));
    expect(screen.queryByText('move', { selector: 'th' })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Check now' }));
    expect(await screen.findByText('move', { selector: 'th' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Resume live updates' }));
    await user.selectOptions(screen.getByLabelText('Show'), 'simulator');
    expect(within(screen.getByTestId('activity-table')).getAllByRole('row')).toHaveLength(2);
  });

  it('stops while the tab is hidden', async () => {
    renderPanel(demo().run, { pollMs: 30 });
    await screen.findByTestId('activity-table');
    const vis = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(screen.getByTestId('activity-status')).toHaveTextContent('Paused while this browser tab is hidden.');
    push(5, 'forward');
    await new Promise((r) => setTimeout(r, 120));
    expect(screen.queryByText('forward', { selector: 'th' })).toBeNull();
    vis.mockReturnValue('visible');
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(await screen.findByText('forward', { selector: 'th' })).toBeInTheDocument();
  });

  it('pages through large backlogs and keeps the cursor when the server returns none', async () => {
    let calls = 0;
    const afters: string[] = [];
    server.use(
      http.get('*/v1/runs/:id/activity', ({ request }) => {
        calls += 1;
        afters.push(new URL(request.url).searchParams.get('after_id') ?? '');
        const items =
          calls === 1
            ? Array.from({ length: 200 }, (_, i) => ({ id: i + 1, actor: 'agent', operation: `op${i + 1}`, outcome: 'ok', interface: 'rest', virtual_time: null, input: {}, result: { n: i } }))
            : [];
        return HttpResponse.json({ items, next_after_id: calls === 1 ? 200 : null });
      }),
    );
    renderPanel({ ...demo().run, status: 'COMPLETED' });
    expect(await screen.findByText('200 actions, newest first')).toBeInTheDocument();
    expect(screen.getByTestId('activity-status')).toHaveTextContent('Not live: the run is COMPLETED');
    expect(screen.queryByRole('button', { name: 'Pause live updates' })).toBeNull();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
    await userEvent.setup().click(screen.getByRole('button', { name: 'Check now' }));
    await waitFor(() => expect(afters).toEqual(['0', '200', '200']));
  });

  it('empty runs and errors', async () => {
    const run = { ...mockDb.runs.run_empty!.run };
    renderPanel(run);
    expect(await screen.findByTestId('empty-state')).toHaveTextContent('No activity yet.');
    server.use(http.get('*/v1/runs/:id/activity', () => fail(500)));
    await userEvent.setup().click(screen.getByRole('button', { name: 'Check now' }));
    expect(await screen.findByText(/Could not load activity/)).toBeInTheDocument();
  });
});

describe('Activity panel concurrency', () => {
  it('ignores overlapping checks and cancels a pending announcement on unmount', async () => {
    let calls = 0;
    server.use(
      http.get('*/v1/runs/:id/activity', async () => {
        calls += 1;
        await delay(200);
        return HttpResponse.json({ items: [], next_after_id: null });
      }),
    );
    const user = renderPanelWithUnmount(demo().run, { pollMs: 100_000 });
    await user.click(screen.getByRole('button', { name: 'Check now' }));
    expect(calls).toBe(1);
    await screen.findByTestId('empty-state');
  });

  it('unmounting with a held-back announcement clears its timer', async () => {
    const spy = vi.spyOn(globalThis, 'clearTimeout');
    renderPanel(demo().run, { pollMs: 30, announceMs: 60_000 });
    await screen.findByTestId('activity-table');
    push(5, 'forward');
    await screen.findByText('forward', { selector: 'th' });
    push(6, 'copy');
    await screen.findByText('copy', { selector: 'th' });
    cleanup();
    expect(spy).toHaveBeenCalled();
  });
});

describe('activity helpers', () => {
  it('labels interfaces and summarises inputs', () => {
    expect(interfaceLabel(undefined)).toBe('Unknown');
    expect(interfaceLabel('graph')).toBe('Microsoft Graph');
    expect(interfaceLabel('carrier-pigeon')).toBe('carrier-pigeon');
    expect(summarizeInput(null)).toBe('');
    expect(summarizeInput({ a: 1, b: { c: 'd' } })).toBe('a=1, b={"c":"d"}');
    const long = summarizeInput(Object.fromEntries(Array.from({ length: 10 }, (_, i) => [`key${i}`, 'v'.repeat(30)])));
    expect(long.length).toBe(120);
    expect(long.endsWith('…')).toBe(true);
    expect(outcomeKind('ok')).toBe('ok');
    expect(outcomeKind('denied')).toBe('denied');
    expect(outcomeKind('NOT_FOUND')).toBe('error-code');
  });
});
