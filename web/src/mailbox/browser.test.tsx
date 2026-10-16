import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { mockDb, server } from '../test/server';
import { OPERATOR_SESSION, renderApp, sessionFor } from '../test/utils';
import { buildMailboxes, mailboxTitle, personaNames } from './useRunMailboxes';
import { formatVirtual, formatVirtualShort } from './mailFormat';
import type { ParsedSnapshot } from './snapshot';

const fail = (status: number, code = 'INTERNAL') =>
  HttpResponse.json({ error: { code, message: 'nope', details: {}, request_id: 'r1' } }, { status });

const rows = () => within(screen.getByTestId('message-list')).getAllByRole('row').slice(1);

describe('Mailbox browser', () => {
  it('UI-5: a same-time reply is listed after its parent in the thread and above it in the list', async () => {
    const demo = mockDb.runs.run_demo!;
    const parent = demo.messages.find((m) => m.id === 'msg_invoice_1')!;
    // id sorts before the parent's, timestamp identical: only ancestry can order them
    demo.messages.push({
      ...parent,
      id: 'aaa_quick_reply',
      message_id_header: '<aaa_quick_reply@sim.test>',
      subject: 'Re: Invoice 4471 (same second)',
      in_reply_to: parent.message_id_header,
      references: [parent.message_id_header],
      attachments: [],
    });
    const { user } = renderApp('/runs/run_demo?tab=mailboxes&message=msg_invoice_1', sessionFor('controller'));
    const view = await screen.findByTestId('message-view');
    const items = within(within(view).getByTestId('thread')).getAllByRole('listitem');
    expect(items[0]).toHaveTextContent('this message'); // the parent (opened) comes first
    expect(items[1]).toHaveTextContent('Re: Invoice 4471 (same second)');
    await user.click(await screen.findByRole('button', { name: 'Load more messages' }));
    await screen.findByText(/26 message\(s\) loaded, newest first/);
    const subjects = rows().map((r) => r.textContent ?? '');
    const iReply = subjects.findIndex((t) => t.includes('(same second)'));
    const iParent = subjects.findIndex((t) => t.includes('Invoice 4471') && !t.includes('Re:'));
    expect(iReply).toBeGreaterThanOrEqual(0);
    expect(iReply).toBeLessThan(iParent); // newest first: the reply sits above its parent
  });

  it('UI-5: attachments download with the operator key via a revoked object URL, never rendered', async () => {
    const saved: Blob[] = [];
    URL.createObjectURL = vi.fn((b: Blob) => {
      saved.push(b);
      return 'blob:att';
    });
    URL.revokeObjectURL = vi.fn();
    const anchors: HTMLAnchorElement[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      anchors.push(this);
    });
    const seen: { url: string; auth: string | null }[] = [];
    server.events.on('request:start', ({ request }) => {
      if (request.url.includes('/attachments/')) seen.push({ url: new URL(request.url).pathname, auth: request.headers.get('authorization') });
    });
    const { user } = renderApp('/runs/run_demo?tab=mailboxes&message=msg_invoice_1', sessionFor('evaluator'));
    const view = await screen.findByTestId('message-view');
    await user.click(within(view).getByRole('button', { name: 'Download invoice-4471.pdf' }));
    await waitFor(() => expect(screen.getByTestId('attachment-status-att_1')).toHaveTextContent('Downloaded invoice-4471.pdf'));
    expect(seen).toEqual([{ url: '/v1/runs/run_demo/mailboxes/mbx_finance/messages/msg_invoice_1/attachments/att_1', auth: 'Bearer mk_evaluator' }]);
    expect(saved[0]?.type).toBe('application/octet-stream');
    expect(anchors[0]?.download).toBe('invoice-4471.pdf');
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:att'), { timeout: 2500 });
    expect(view.querySelector('iframe[src^="blob:"], object, embed')).toBeNull();
    server.events.removeAllListeners();

    // no Content-Disposition: the listed filename; failures are reported per attachment
    server.use(http.get('*/v1/runs/:id/mailboxes/:mb/messages/:m/attachments/:a', () => new HttpResponse(new Uint8Array([1]))));
    await user.click(within(view).getByRole('button', { name: 'Download invoice-4471.pdf' }));
    await waitFor(() => expect(anchors[1]?.download).toBe('invoice-4471.pdf'));
    server.use(http.get('*/v1/runs/:id/mailboxes/:mb/messages/:m/attachments/:a', () => fail(404, 'NOT_FOUND')));
    await user.click(within(view).getByRole('button', { name: 'Download invoice-4471.pdf' }));
    expect(await within(view).findByTestId('api-error')).toHaveTextContent('Could not download invoice-4471.pdf: NOT_FOUND (HTTP 404)');
  });

  it('UI-5: an unnamed attachment without a server filename is saved as attachment-<id>', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:u');
    URL.revokeObjectURL = vi.fn();
    const anchors: HTMLAnchorElement[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      anchors.push(this);
    });
    const invoice = mockDb.runs.run_demo!.messages.find((m) => m.id === 'msg_invoice_1')!;
    invoice.attachments = [{ ...invoice.attachments![0]!, id: 'att_x', filename: '' }];
    server.use(http.get('*/v1/runs/:id/mailboxes/:mb/messages/:m/attachments/:a', () => new HttpResponse(new Uint8Array([1]))));
    const { user } = renderApp('/runs/run_demo?tab=mailboxes&message=msg_invoice_1', sessionFor('controller'));
    const view = await screen.findByTestId('message-view');
    await user.click(within(view).getByRole('button', { name: 'Download (unnamed)' }));
    await waitFor(() => expect(anchors[0]?.download).toBe('attachment-att_x'));
  });

  it('lists every mailbox with the agent marked, folders with counts, paging, folders and the reading pane', async () => {
    const { user, router } = renderApp('/runs/run_demo?tab=mailboxes', sessionFor('controller'));
    expect(await screen.findByText('20 message(s) loaded, more available, newest first')).toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: 'Mailboxes and folders' });
    const agent = within(nav).getByTestId('mailbox-mbx_finance');
    // controller: display names from the scenario document
    await waitFor(() => expect(agent).toHaveTextContent('Asha Rao (Finance Manager)'));
    expect(agent).toHaveTextContent('Agent under test');
    expect(agent).toHaveTextContent('25 messages, 24 unread');
    expect(within(nav).getByTestId('mailbox-mbx_vendor')).not.toHaveTextContent('Agent under test');
    expect(within(nav).getByTestId('mailbox-mbx_vendor')).toHaveTextContent('1 message, 1 unread');
    expect(screen.getByText('This is the mailbox the agent under test works in.', { exact: false })).toBeInTheDocument();

    // newest first, unread / attachment indicators
    expect(rows()[0]).toHaveTextContent('Acme Finance');
    expect(within(rows()[1] as HTMLElement).getByText('Unread')).toBeInTheDocument();
    expect(within(rows()[2] as HTMLElement).getByText('Has attachments')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Load more messages' }));
    expect(await screen.findByText('25 message(s) loaded, newest first')).toBeInTheDocument();

    await user.click(within(nav).getByRole('button', { name: /^sent, 1 message \(Asha Rao/ }));
    expect(await screen.findByText('1 message(s) loaded, newest first')).toBeInTheDocument();
    expect(router.state.location.search).toContain('folder=mbx_finance_sent');
    expect(screen.getByRole('heading', { name: /Asha Rao \(Finance Manager\) · sent/ })).toBeInTheDocument();
    await user.click(within(nav).getByRole('button', { name: /^archive, 0 messages \(Asha/ }));
    expect(await screen.findByText('No messages in this folder.')).toBeInTheDocument();
    await user.click(within(nav).getByRole('button', { name: /Asha Rao \(Finance Manager\)Agent under test/ }));
    await screen.findByText(/25 message\(s\) loaded/);
    expect(router.state.location.search).not.toContain('folder=');

    await user.click(screen.getByRole('button', { name: 'Invoice 4471' }));
    const view = await screen.findByTestId('message-view');
    expect(within(view).getByRole('heading', { level: 3 })).toHaveTextContent('Invoice 4471');
    expect(within(view).getByRole('heading', { level: 3 })).toHaveFocus();
    expect(within(view).getByTestId('attachments')).toHaveTextContent('invoice-4471.pdf');
    expect(within(view).getByTestId('attachments')).toHaveTextContent('application/pdf · 47.1 KB');
    expect(within(view).getByText('1 attachment')).toBeInTheDocument();
    expect(router.state.location.search).toContain('message=msg_invoice_1');

    // headers toggle
    expect(within(view).queryByTestId('message-headers')).toBeNull();
    await user.click(within(view).getByRole('button', { name: 'Show headers' }));
    const headers = within(view).getByTestId('message-headers');
    expect(headers).toHaveTextContent('<msg_invoice_1@sim.test>');
    expect(headers).toHaveTextContent('SupplierERP 9');
    await user.click(within(view).getByRole('button', { name: 'Hide headers' }));

    // thread: expand another message, then open it
    const thread = within(view).getByTestId('thread');
    expect(within(thread).getAllByRole('listitem')).toHaveLength(3);
    expect(thread).toHaveTextContent('this message');
    const expand = within(thread).getAllByRole('button', { name: /^Expand/ })[0] as HTMLElement;
    await user.click(expand);
    expect(within(thread).getByRole('button', { name: /^Collapse/ })).toHaveAttribute('aria-expanded', 'true');
    expect(within(thread).getAllByTestId('email-text')[0]).toHaveTextContent('new bank account');
    await user.click(within(thread).getByRole('button', { name: /^Collapse/ }));
    await user.click(within(thread).getAllByRole('button', { name: /^Open Re: Invoice/ })[0] as HTMLElement);
    await waitFor(() => expect(router.state.location.search).toContain('message=msg_html_1'));
    const view2 = await screen.findByTestId('message-view');
    await user.click(within(view2).getByRole('button', { name: 'Show headers' }));
    expect(within(view2).getByText('In-Reply-To')).toBeInTheDocument();
    expect(within(view2).getByText('References')).toBeInTheDocument();
    expect(within(view2).getByText('IMPORTANT')).toBeInTheDocument();
    expect(document.querySelectorAll('script')).toHaveLength(0);
    await user.click(within(view2).getByTestId('raw-mime-link'));
    expect(router.state.location.search).toContain('tab=artifacts');
    expect(await screen.findByTestId('highlighted-artifact')).toHaveTextContent('blob/raw/msg_html_1.eml');
  });

  it('evaluator: role names without display names, other mailbox, single-message thread, no inject', async () => {
    const { user } = renderApp('/runs/run_demo?tab=mailboxes&mailbox=mbx_vendor', sessionFor('evaluator'));
    expect(await screen.findByText('1 message(s) loaded, newest first')).toBeInTheDocument();
    expect(screen.getByTestId('mailbox-mbx_vendor')).toHaveTextContent('Vendor');
    expect(screen.getByText(/A persona mailbox \(billing@supplier.test\)/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Inject email here' })).toBeNull();
    expect(screen.getByText('Select a message to read it with its thread, attachments and headers.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Invoice 4471 - updated bank details/ }));
    expect(await screen.findByText('This message is the only one in its thread.')).toBeInTheDocument();
  });

  it('message deep links pick the owning mailbox; unknown ids explain themselves', async () => {
    const { unmount } = renderApp('/runs/run_demo?tab=mailboxes&message=msg_vendor_in_1', sessionFor('evaluator'));
    expect(await screen.findByTestId('message-view')).toHaveTextContent('verify the change by phone');
    expect(screen.getByRole('heading', { name: /Vendor · All folders/ })).toBeInTheDocument();
    unmount();
    renderApp('/runs/run_demo?tab=mailboxes&message=msg_nope', sessionFor('evaluator'));
    expect(await screen.findByText(/Message msg_nope is not in this run/)).toBeInTheDocument();
  });

  it('empty runs, state errors, mailbox list errors and message list errors', async () => {
    server.use(
      http.get('*/v1/runs/:id/state', () => HttpResponse.json({ mailboxes: [] })),
      http.get('*/v1/runs/:id/mailboxes', () => HttpResponse.json([])),
    );
    const first = renderApp('/runs/run_demo?tab=mailboxes', sessionFor('evaluator'));
    expect(await screen.findByText('This run has no mailboxes.')).toBeInTheDocument();
    first.unmount();

    server.use(http.get('*/v1/runs/:id/state', () => fail(500)));
    const second = renderApp('/runs/run_demo?tab=mailboxes', sessionFor('evaluator'));
    expect(await screen.findByText(/Could not load run state/)).toBeInTheDocument();
    second.unmount();
    server.resetHandlers();

    server.use(http.get('*/v1/runs/:id/mailboxes', () => fail(500)));
    const third = renderApp('/runs/run_demo?tab=mailboxes', sessionFor('evaluator'));
    expect(await screen.findByText(/Could not load the run’s mailboxes/)).toBeInTheDocument();
    third.unmount();
    server.resetHandlers();

    server.use(http.get('*/v1/runs/:id/mailboxes/:mb/messages', () => fail(500)));
    renderApp('/runs/run_demo?tab=mailboxes', sessionFor('evaluator'));
    expect(await screen.findByText(/Could not load messages/)).toBeInTheDocument();
  });

  it('a later page failing keeps the loaded rows', async () => {
    let calls = 0;
    server.use(
      http.get('*/v1/runs/:id/mailboxes/:mb/messages', () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ items: [{ id: 'x1', mailbox_id: 'mbx_finance', thread_id: 't', folder: 'inbox', from: { email: 'a@x' }, subject: '', created_at: '2026-03-02T09:00:00Z', flags: ['SEEN', 'FLAGGED'] }], next_cursor: 'c2' })
          : fail(500);
      }),
    );
    const { user } = renderApp('/runs/run_demo?tab=mailboxes', sessionFor('evaluator'));
    expect(await screen.findByText('Flagged')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '(no subject)' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Load more messages' }));
    expect(await screen.findByText(/Could not load more messages/)).toBeInTheDocument();
    expect(rows()).toHaveLength(1);
  });

  it('works without the run.created event and without a scenario document', async () => {
    server.use(
      http.get('*/v1/runs/:id/events', () => HttpResponse.json({ items: [], next_cursor: null })),
      http.get('*/v1/scenarios/:key/versions/:v/document', () => fail(403, 'PERMISSION_DENIED')),
    );
    mockDb.localNoAuth = true;
    renderApp('/runs/run_demo?tab=mailboxes', OPERATOR_SESSION);
    const nav = await screen.findByRole('navigation', { name: 'Mailboxes and folders' });
    expect(within(nav).getByTestId('mailbox-mbx_finance')).toHaveTextContent('Finance Manager');
    expect(nav).not.toHaveTextContent('Agent under test');
  });
});

describe('Inject email from a mailbox', () => {
  it('composes from a persona, threads a reply, validates and delivers', async () => {
    const { user, router } = renderApp('/runs/run_demo?tab=mailboxes', sessionFor('controller'));
    await screen.findByText(/20 message\(s\) loaded/);
    await user.click(screen.getByRole('button', { name: 'Inject email here' }));
    const panel = await screen.findByTestId('compose-inject');
    expect(within(panel).getByRole('heading', { level: 3 })).toHaveFocus();
    const form = within(panel).getByRole('form', { name: 'Inject email' });
    // default sender: another persona; default recipient: this mailbox
    await waitFor(() => expect(within(form).getByLabelText('From')).toHaveValue('billing@supplier.test'));
    const to = within(form).getByRole('group', { name: 'To' });
    expect(within(to).getByRole('checkbox', { name: /finance@acme.test/ })).toBeChecked();

    // validation: no recipients and no body
    await user.click(within(to).getByRole('checkbox', { name: /finance@acme.test/ }));
    await user.click(within(form).getByRole('button', { name: 'Deliver email' }));
    const v = await within(panel).findByTestId('compose-validation');
    expect(v).toHaveTextContent('Fix 2 field(s)');
    expect(within(to).getByText('At least one recipient is required.')).toBeInTheDocument();

    await user.click(within(to).getByRole('checkbox', { name: /finance@acme.test/ }));
    await user.click(within(to).getByRole('checkbox', { name: /billing@supplier.test/ }));
    await user.click(within(to).getByRole('checkbox', { name: /billing@supplier.test/ }));
    await user.type(within(to).getByLabelText(/Other recipients/), 'ops@acme.test');
    const cc = within(form).getByRole('group', { name: 'Cc' });
    await user.click(within(cc).getByRole('checkbox', { name: /billing@supplier.test/ }));
    await user.type(within(cc).getByLabelText(/Other Cc addresses/), 'audit@acme.test');
    await user.selectOptions(within(form).getByLabelText('In reply to (threading)'), 'msg_invoice_1');
    expect(within(form).getByLabelText('Subject')).toHaveValue('Re: Invoice 4471');
    await user.type(within(form).getByLabelText('Text body'), 'Updated remittance');
    await user.type(within(form).getByLabelText(/HTML body/), '<b>x</b>');

    let sent: Record<string, unknown> | null = null;
    server.events.on('request:start', async ({ request }) => {
      if (request.url.includes('/inject')) sent = (await request.clone().json()) as Record<string, unknown>;
    });
    await user.click(within(form).getByRole('button', { name: 'Deliver email' }));
    const result = await within(panel).findByTestId('compose-result');
    await waitFor(() => expect(result).toHaveTextContent('Delivered to finance@acme.test.'));
    server.events.removeAllListeners();
    expect(sent).toMatchObject({
      message: {
        from: 'billing@supplier.test',
        to: ['finance@acme.test', 'ops@acme.test'],
        cc: ['billing@supplier.test', 'audit@acme.test'],
        subject: 'Re: Invoice 4471',
        in_reply_to: '<msg_invoice_1@sim.test>',
        references: ['<msg_invoice_1@sim.test>'],
        html: '<b>x</b>',
      },
    });
    await user.click(within(result).getByRole('button', { name: 'Open the delivered email' }));
    await waitFor(() => expect(router.state.location.search).toContain('message=msg_inj_1'));
    expect(await screen.findByTestId('message-view')).toHaveTextContent('Updated remittance');
  });

  it('external senders, keeps an existing Re: subject, close, and server errors', async () => {
    mockDb.localNoAuth = true;
    const { user } = renderApp('/runs/run_demo?tab=mailboxes', OPERATOR_SESSION);
    await screen.findByText(/20 message\(s\) loaded/);
    await user.click(screen.getByRole('button', { name: 'Inject email here' }));
    let panel = await screen.findByTestId('compose-inject');
    await user.click(within(panel).getByRole('button', { name: 'Close' }));
    expect(screen.queryByTestId('compose-inject')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Inject email here' }));
    panel = await screen.findByTestId('compose-inject');
    const form = within(panel).getByRole('form', { name: 'Inject email' });
    await user.selectOptions(within(form).getByLabelText('From'), 'Someone outside the scenario…');
    await user.type(within(form).getByLabelText('Text body'), 'hi');
    await user.click(within(form).getByRole('button', { name: 'Deliver email' }));
    expect(await within(panel).findAllByText('Sender address is required.')).toHaveLength(2);
    expect(within(form).getByLabelText('Sender address')).toHaveAttribute('aria-invalid', 'true');

    await user.type(within(form).getByLabelText('Subject'), 'Mine');
    await user.selectOptions(within(form).getByLabelText('In reply to (threading)'), 'msg_html_1');
    expect(within(form).getByLabelText('Subject')).toHaveValue('Mine');
    await user.clear(within(form).getByLabelText('Subject'));
    await user.selectOptions(within(form).getByLabelText('In reply to (threading)'), '');
    await user.selectOptions(within(form).getByLabelText('In reply to (threading)'), 'msg_html_1');
    expect(within(form).getByLabelText('Subject')).toHaveValue('Re: Invoice 4471 - updated bank details');

    await user.type(within(form).getByLabelText('Sender address'), 'not-an-email');
    await user.click(within(form).getByRole('button', { name: 'Deliver email' }));
    expect(await within(panel).findByText(/The email was not delivered/)).toBeInTheDocument();
    // picking a message closes the composer
    await user.click(screen.getByRole('button', { name: 'Invoice 4471' }));
    expect(await screen.findByTestId('message-view')).toBeInTheDocument();
    expect(screen.queryByTestId('compose-inject')).toBeNull();
  });

  it('delivers an HTML-only email', async () => {
    const { user } = renderApp('/runs/run_demo?tab=mailboxes', sessionFor('controller'));
    await screen.findByText(/20 message\(s\) loaded/);
    await user.click(screen.getByRole('button', { name: 'Inject email here' }));
    const panel = await screen.findByTestId('compose-inject');
    await user.type(within(panel).getByLabelText(/HTML body/), '<p>only html</p>');
    await user.click(within(panel).getByRole('button', { name: 'Deliver email' }));
    await waitFor(() => expect(within(panel).getByTestId('compose-result')).toHaveTextContent('Delivered'));
    expect(mockDb.runs.run_demo!.messages.at(-1)).toMatchObject({ body_text: null, body_html: '<p>only html</p>' });
  });

  it('a mailbox with no other persona defaults to an external sender', async () => {
    server.use(
      http.get('*/v1/runs/:id/mailboxes', () => HttpResponse.json([{ id: 'mbx_finance', owner_id: 'per_finance', email: 'finance@acme.test' }])),
    );
    const { user } = renderApp('/runs/run_demo?tab=mailboxes', sessionFor('controller'));
    await screen.findByText(/20 message\(s\) loaded/);
    await user.click(screen.getByRole('button', { name: 'Inject email here' }));
    expect(await screen.findByLabelText('Sender address')).toBeInTheDocument();
  });
});

describe('mailbox helpers', () => {
  const snap: ParsedSnapshot = {
    mailboxes: [
      { id: 'b', email: 'b@x', owner_id: 'pb' },
      { id: 'a', email: 'a@x', owner_id: '' },
    ],
    personas: [{ id: 'pb', email: 'b@x', role: '' }],
    folders: [
      { id: 'f1', mailbox_id: 'b', name: 'Custom', system_type: null },
      { id: 'f2', mailbox_id: 'b', name: 'Zed', system_type: null },
      { id: 'f3', mailbox_id: 'b', name: 'INBOX', system_type: 'inbox' },
    ],
    messages: [],
  };

  it('orders folders (system first, then by name) and falls back to the snapshot mailboxes', () => {
    const out = buildMailboxes(snap, undefined, null, {});
    expect(out.map((m) => m.id)).toEqual(['a', 'b']);
    expect(out[1]?.folders.map((f) => f.name)).toEqual(['INBOX', 'Custom', 'Zed']);
    expect(mailboxTitle(out[0]!)).toBe('a@x');
    expect(mailboxTitle(out[1]!)).toBe('pb');
    const listed = buildMailboxes(snap, [{ id: 'b', email: 'b@x', owner_id: '' }], 'pb', { pb: 'Bea' });
    expect(listed[0]).toMatchObject({ ownerId: 'pb', isAgent: true, name: 'Bea' });
    expect(mailboxTitle(listed[0]!)).toBe('Bea');
  });

  it('reads persona names defensively', () => {
    expect(personaNames(null)).toEqual({});
    expect(personaNames({ scenario: { personas: [{ id: 'p', name: 'P' }, { id: 'q' }, { name: 'x' }] } })).toEqual({ p: 'P' });
  });

  it('formats virtual time', () => {
    expect(formatVirtual(null)).toBe('—');
    expect(formatVirtual('garbage')).toBe('garbage');
    expect(formatVirtual('2026-03-02T09:05:00Z')).toBe('2 Mar 2026, 09:05 UTC');
    expect(formatVirtualShort('2026-03-02T09:05:00Z')).toBe('2 Mar, 09:05');
    expect(formatVirtualShort('garbage')).toBe('garbage');
  });
});

describe('review fixes', () => {
  it('the reading pane uses the listed message when the state snapshot does not have it yet', async () => {
    const r = mockDb.runs.run_demo!;
    server.use(
      http.get('*/v1/runs/:id/state', () =>
        HttpResponse.json({
          mailboxes: r.mailboxes.map(({ id, email, owner_id }) => ({ id, email, owner_id })),
          folders: r.mailboxes.flatMap((mb) => mb.folders.map((f) => ({ ...f, mailbox_id: mb.id }))),
          messages: [],
        }),
      ),
    );
    const { user } = renderApp('/runs/run_demo?tab=mailboxes', sessionFor('evaluator'));
    await screen.findByText(/20 message\(s\) loaded/);
    await user.click(screen.getByRole('button', { name: 'Invoice 4471' }));
    const view = await screen.findByTestId('message-view');
    expect(view).toHaveTextContent('invoice-4471.pdf');
    expect(screen.queryByText(/is not in this run/)).toBeNull();
  });

  it('warns about recipients outside the scenario and shows the server policy error', async () => {
    server.use(
      http.post('*/v1/runs/:id/mailboxes/:mb/inject', () =>
        HttpResponse.json(
          { error: { code: 'PERMISSION_DENIED', message: 'external delivery is denied by the scenario', details: {}, request_id: 'r9' } },
          { status: 403 },
        ),
      ),
    );
    const { user } = renderApp('/runs/run_demo?tab=mailboxes', sessionFor('controller'));
    await screen.findByText(/20 message\(s\) loaded/);
    await user.click(screen.getByRole('button', { name: 'Inject email here' }));
    const panel = await screen.findByTestId('compose-inject');
    expect(within(panel).queryByTestId('compose-outside')).toBeNull();
    await user.type(within(panel).getByLabelText(/Other recipients/), 'a@outside.test, b@outside.test');
    expect(within(panel).getByTestId('compose-outside')).toHaveTextContent('a@outside.test, b@outside.test are outside the scenario');
    await user.clear(within(panel).getByLabelText(/Other recipients/));
    await user.type(within(panel).getByLabelText(/Other Cc addresses/), 'c@outside.test');
    expect(within(panel).getByTestId('compose-outside')).toHaveTextContent('c@outside.test is outside the scenario');
    await user.type(within(panel).getByLabelText('Text body'), 'x');
    await user.click(within(panel).getByRole('button', { name: 'Deliver email' }));
    expect(await within(panel).findByText('external delivery is denied by the scenario')).toBeInTheDocument();
  });
});
