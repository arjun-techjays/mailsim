import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { rememberRun } from '../auth/storage';
import { server } from '../test/server';
import { renderApp, sessionFor } from '../test/utils';

describe('Runs page', () => {
  it('opens a run by id and lists recent runs', async () => {
    rememberRun({ id: 'run_demo', scenarioKey: 'finance.invoice-bank-change' });
    rememberRun({ id: 'run_other', scenarioKey: null });
    const { user, router } = renderApp('/runs', sessionFor('controller'));
    const recent = screen.getByTestId('recent-runs');
    expect(within(recent).getAllByRole('listitem')).toHaveLength(2);
    expect(recent).toHaveTextContent('(finance.invoice-bank-change)');

    await user.click(screen.getByRole('button', { name: 'Open run' }));
    expect(screen.getByText('Enter a run id.')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Run id'), ' run_demo ');
    await user.click(screen.getByRole('button', { name: 'Open run' }));
    expect(router.state.location.pathname).toBe('/runs/run_demo');
  });

  it('shows an empty state', () => {
    renderApp('/runs', sessionFor('controller'));
    expect(screen.getByTestId('empty-state')).toHaveTextContent('No runs opened yet.');
  });
});

describe('Agent mailbox', () => {
  it('opens the bound mailbox with server-side cursor pagination', async () => {
    const urls: string[] = [];
    server.events.on('request:start', ({ request }) => {
      urls.push(request.url);
    });
    const { user, router } = renderApp('/mailbox', sessionFor('agent'));
    await user.click(screen.getByRole('button', { name: 'Open mailbox' }));
    expect(screen.getByText('Enter the mailbox id from the agent credential.')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Mailbox id'), 'mbx_finance');
    await user.click(screen.getByRole('button', { name: 'Open mailbox' }));
    expect(router.state.location.pathname).toBe('/mailbox/mbx_finance');

    expect(await screen.findByText('20 message(s) loaded, more available')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Load more messages' }));
    expect(await screen.findByText('25 message(s) loaded')).toBeInTheDocument();
    expect(urls.some((u) => u.includes('/v1/mailboxes/mbx_finance/messages?cursor=20&page_size=20'))).toBe(true);

    await user.click(within(screen.getByRole('navigation', { name: 'Folders' })).getByRole('button', { name: 'inbox' }));
    expect(await screen.findByText('20 message(s) loaded, more available')).toBeInTheDocument();
    expect(urls.some((u) => u.includes('folder=inbox'))).toBe(true);

    await user.click(screen.getByRole('button', { name: 'Invoice 4471' }));
    expect(await screen.findByTestId('message-view')).toHaveTextContent('invoice-4471.pdf');
    expect(screen.getByTestId('message-view')).toHaveTextContent('blob/raw/msg_invoice_1.eml');
    expect(screen.queryByTestId('raw-mime-link')).toBeNull();
    expect(router.state.location.search).toBe('?message=msg_invoice_1');
    server.events.removeAllListeners();
  });

  it('another mailbox is refused', async () => {
    renderApp('/mailbox/mbx_vendor', sessionFor('agent'));
    expect((await screen.findAllByTestId('api-error'))[0]).toHaveTextContent('PERMISSION_DENIED');
  });

  it('thread and message-list errors; messages without optional fields', async () => {
    server.use(
      http.get('*/v1/mailboxes/:id/threads/:t', () => HttpResponse.error()),
      http.get('*/v1/mailboxes/:id/messages', () =>
        HttpResponse.json({
          items: [
            {
              id: 'm1',
              mailbox_id: 'mbx_finance',
              message_id_header: '<m1>',
              thread_id: 't1',
              folder: 'inbox',
              from: { email: 'a@x' },
              created_at: '2026-01-01T00:00:00Z',
            },
          ],
          next_cursor: null,
        }),
      ),
      http.get('*/v1/mailboxes/:id/messages/:m', () =>
        HttpResponse.json({
          id: 'm1',
          mailbox_id: 'mbx_finance',
          message_id_header: '<m1>',
          thread_id: 't1',
          folder: 'inbox',
          from: { email: 'a@x' },
          created_at: '2026-01-01T00:00:00Z',
        }),
      ),
    );
    const { user } = renderApp('/mailbox/mbx_finance', sessionFor('agent'));
    await user.click(await screen.findByRole('button', { name: '(no subject)' }));
    const view = await screen.findByTestId('message-view');
    expect(view).toHaveTextContent('No attachments.');
    expect(view).toHaveTextContent('This message has no body.');
    expect(await within(view).findByText(/Could not load thread/)).toBeInTheDocument();
  });

  it('folder and list errors', async () => {
    server.use(
      http.get('*/v1/mailboxes/:id/folders', () => HttpResponse.error()),
      http.get('*/v1/mailboxes/:id/messages', () => HttpResponse.error()),
    );
    renderApp('/mailbox/mbx_finance', sessionFor('agent'));
    expect(await screen.findByText(/Could not load folders/)).toBeInTheDocument();
    expect(await screen.findByText(/Could not load messages/)).toBeInTheDocument();
  });

  it('second page failure keeps loaded messages', async () => {
    server.use(
      http.get('*/v1/mailboxes/:id/messages', ({ request }) =>
        new URL(request.url).searchParams.get('cursor')
          ? HttpResponse.error()
          : HttpResponse.json({
              items: [
                {
                  id: 'm1',
                  mailbox_id: 'mbx_finance',
                  message_id_header: '<m1>',
                  thread_id: 't1',
                  folder: 'inbox',
                  subject: 'One',
                  from: { email: 'a@x' },
                  created_at: '2026-01-01T00:00:00Z',
                },
              ],
              next_cursor: 'n',
            }),
      ),
    );
    const { user } = renderApp('/mailbox/mbx_finance', sessionFor('agent'));
    await user.click(await screen.findByRole('button', { name: 'Load more messages' }));
    expect(await screen.findByText(/Could not load more messages/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'One' })).toBeInTheDocument();
  });

  it('is gated for non-agent keys', () => {
    renderApp('/mailbox', sessionFor('controller'));
    expect(screen.getByTestId('role-gate')).toHaveTextContent('requires an agent key');
  });
});
