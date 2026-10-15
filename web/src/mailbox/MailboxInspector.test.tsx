import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { MemoryRouter } from 'react-router-dom';
import type { CanonicalMessage } from '../api/types';
import { makeQueryClient } from '../queryClient';
import { MailboxInspector } from './MailboxInspector';
import type { MailboxSource } from './source';

// subject/is_draft are deliberately absent: the inspector must tolerate sparse payloads.
const bare = {
  id: 'm1',
  mailbox_id: 'mb',
  message_id_header: '<m1>',
  thread_id: 't1',
  folder: 'inbox',
  from: { email: 'a@x' },
  created_at: '2026-01-01T00:00:00Z',
} as unknown as CanonicalMessage;
const full: CanonicalMessage = {
  ...bare,
  id: 'm2',
  subject: 'Full',
  to: [{ email: 'b@x' }],
  cc: [{ email: 'c@x', name: 'C' }],
  labels: ['L'],
  flags: ['SEEN'],
  received_at: '2026-01-02T00:00:00Z',
  raw_blob_ref: 'blob/m2',
  attachments: [],
};

function source(overrides: Partial<MailboxSource> = {}): MailboxSource {
  return {
    key: ['fake', Math.random()],
    listFolders: async () => [{ id: 'f', name: 'inbox', system_type: 'inbox' }],
    listMessages: async () => ({ items: [bare, full], next_cursor: null }),
    getThread: async () => [bare, full],
    getMessage: async (id) => (id === 'm1' ? bare : full),
    ...overrides,
  };
}

function Harness({ src, initial = null }: { src: MailboxSource; initial?: string | null }) {
  const [sel, setSel] = useState<string | null>(initial);
  return <MailboxInspector source={src} selectedMessage={sel} onSelectMessage={setSel} />;
}

function renderInspector(src: MailboxSource, initial: string | null = null) {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <MemoryRouter>
        <Harness src={src} initial={initial} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('MailboxInspector', () => {
  it('renders sparse and full messages; focus moves only on user selection', async () => {
    const user = userEvent.setup();
    renderInspector(source(), 'm1');
    const view = await screen.findByTestId('message-view');
    const heading = within(view).getByRole('heading', { level: 3 });
    expect(heading).toHaveTextContent('(no subject)');
    expect(heading).not.toHaveFocus(); // deep link / initial selection: no focus jump
    expect(view).toHaveTextContent('No attachments.');
    expect(within(view).getByText('Raw MIME').nextElementSibling).toHaveTextContent('—');
    const thread = await within(view).findByTestId('thread');
    expect(thread).toHaveTextContent('(no subject) — a@x (this message)');

    await user.click(within(thread).getByRole('button', { name: /Full/ }));
    await waitFor(() => expect(within(screen.getByTestId('message-view')).getByRole('heading', { level: 3 })).toHaveTextContent('Full'));
    await waitFor(() => expect(within(screen.getByTestId('message-view')).getByRole('heading', { level: 3 })).toHaveFocus());
    const v2 = screen.getByTestId('message-view');
    expect(v2).toHaveTextContent('C <c@x>');
    expect(v2).toHaveTextContent('L, SEEN');
    expect(v2).toHaveTextContent('2026-01-02T00:00:00Z');
    expect(within(v2).getByText('blob/m2').tagName).toBe('CODE'); // no link target without rawMimeHref
    expect(await within(v2).findByRole('button', { name: /no subject/ })).toBeInTheDocument();

    const list = screen.getByTestId('message-list');
    expect(within(list).getByRole('button', { name: '(no subject)' })).toBeInTheDocument();
    expect(within(list).getByRole('button', { name: 'Full' })).toHaveAttribute('aria-current', 'true');
  });

  it('shows folder, list and thread errors', async () => {
    renderInspector(
      source({
        listFolders: async () => Promise.reject(new Error('folders down')),
        listMessages: async () => Promise.reject(new Error('list down')),
        getThread: async () => Promise.reject(new Error('thread down')),
      }),
      'm1',
    );
    expect(await screen.findByText(/Could not load folders/)).toBeInTheDocument();
    expect(await screen.findByText(/Could not load messages/)).toBeInTheDocument();
    expect(await screen.findByText(/Could not load thread/)).toBeInTheDocument();
  });

  it('keeps loaded messages when a later page fails, and shows the loading label', async () => {
    const user = userEvent.setup();
    let release: () => void = () => {};
    const listMessages = vi.fn(async (_f: string | null, cursor: string | null) => {
      if (!cursor) return { items: [full], next_cursor: 'c2' };
      if (cursor === 'c2') {
        await new Promise<void>((r) => (release = r));
        return { items: [], next_cursor: 'c3' };
      }
      throw new Error('page 3 down');
    });
    renderInspector(source({ listMessages }));
    await user.click(await screen.findByRole('button', { name: 'Load more messages' }));
    expect(await screen.findByRole('button', { name: 'Loading…' })).toBeDisabled();
    release();
    await user.click(await screen.findByRole('button', { name: 'Load more messages' }));
    expect(await screen.findByText(/Could not load more messages/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Full' })).toBeInTheDocument();
    expect(screen.getByText('Select a message to see its headers, body, attachments and thread.')).toBeInTheDocument();
  });
});
