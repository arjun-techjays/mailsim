import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import type { CanonicalMessage } from '../api/types';
import { deliveredIndex } from '../run/toolbarDiff';
import { ReadingPane } from './ReadingPane';
import { snippet, subjectOf } from './mailFormat';
import { VirtualTime } from './VirtualTime';

const base = {
  id: 'm1',
  mailbox_id: 'mb',
  message_id_header: '<m1@x>',
  thread_id: 't',
  folder: 'inbox',
  from: { email: 'a@x' },
  created_at: '2026-03-02T09:00:00Z',
} as unknown as CanonicalMessage;

function show(message: CanonicalMessage, thread: CanonicalMessage[], rawMimeHref?: (r: string) => string) {
  const user = userEvent.setup();
  const onOpen = vi.fn();
  render(
    <MemoryRouter>
      <ReadingPane message={message} thread={thread} focusOnLoad={false} onOpen={onOpen} rawMimeHref={rawMimeHref} />
    </MemoryRouter>,
  );
  return { user, onOpen };
}

describe('ReadingPane', () => {
  it('handles a sparse message (no subject, body, headers, raw MIME or attachments)', async () => {
    const { user } = show(base, [base]);
    const view = screen.getByTestId('message-view');
    expect(within(view).getByRole('heading', { level: 3 })).toHaveTextContent('(no subject)');
    expect(within(view).getByRole('heading', { level: 3 })).not.toHaveFocus();
    expect(view).toHaveTextContent('To—');
    expect(screen.queryByTestId('attachments')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Show headers' }));
    const h = screen.getByTestId('message-headers');
    expect(h).toHaveTextContent('No other headers recorded.');
    expect(h).toHaveTextContent('Labels / flags—');
    expect(h).toHaveTextContent('Raw MIME—');
    expect(within(h).queryByText('References')).toBeNull();
  });

  it('shows cc, many attachments, raw MIME without a link target, and a thread with collapsed snippets', async () => {
    const m: CanonicalMessage = {
      ...base,
      subject: 'Hello',
      cc: [{ email: 'c@x', name: 'C' }],
      received_at: '2026-03-02T09:05:00Z',
      references: ['<r0@x>'],
      raw_blob_ref: 'blob/raw/m1.eml',
      attachments: [
        { id: 'a1', filename: '', mime_type: 'image/png', size: 10, checksum: '', blob_ref: '', inline: true, content_id: 'c' },
        { id: 'a2', filename: 'b.pdf', mime_type: 'application/pdf', size: 2048, checksum: '', blob_ref: '', inline: false, content_id: null },
      ],
    };
    const other: CanonicalMessage = { ...base, id: 'm0', body_text: 'x '.repeat(100), received_at: null };
    const blank: CanonicalMessage = { ...base, id: 'm2', body_text: null };
    const { user, onOpen } = show(m, [other, m, blank]);
    expect(screen.getByText('Cc')).toBeInTheDocument();
    expect(screen.getByText('2 attachments')).toBeInTheDocument();
    expect(screen.getByTestId('attachments')).toHaveTextContent('(unnamed) image/png · 10 B · inline');
    await user.click(screen.getByRole('button', { name: 'Show headers' }));
    expect(screen.getByText('blob/raw/m1.eml').tagName).toBe('CODE');
    expect(screen.getByText('<r0@x>')).toBeInTheDocument();
    expect(screen.getByText('(no text)')).toBeInTheDocument();
    expect(screen.getByText(/…$/)).toBeInTheDocument();
    await user.click(screen.getAllByRole('button', { name: /Open \(no subject\)/ })[0] as HTMLElement);
    expect(onOpen).toHaveBeenCalledWith('m0');
  });
});

describe('small helpers', () => {
  it('formats subjects, snippets, times and delivered messages', () => {
    expect(subjectOf({ subject: '' })).toBe('(no subject)');
    expect(subjectOf({ subject: null })).toBe('(no subject)');
    expect(snippet({ body_text: null })).toBe('');
    render(<VirtualTime value={null} />);
    expect(document.body).toHaveTextContent('—');
    const idx = deliveredIndex({
      mailboxes: [{ id: 'mb', email: 'a@x', owner_id: '' }],
      personas: [],
      folders: [],
      messages: [
        { ...base, subject: 's' },
        { ...base, id: 'm9', mailbox_id: 'gone' },
      ],
    });
    expect(idx.get('m1')).toEqual({ id: 'm1', subject: 's', to: 'a@x' });
    expect(idx.get('m9')?.to).toBe('gone');
  });
});
