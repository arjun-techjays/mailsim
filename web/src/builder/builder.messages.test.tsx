import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { captureBodies, goSection, openNew, SLOW, waitValid } from '../test/builder';
import { server } from '../test/server';
import { renderApp, sessionFor } from '../test/utils';
import { draftStorageKey, saveDraft } from './draft';
import { textToBase64 } from './files';
import { templateDraft } from './template';

afterEach(() => server.events.removeAllListeners());
vi.setConfig({ testTimeout: 30_000 });

const EML = ['From: Ann <ann@outside.test>', 'To: finance@example.test', 'Subject: Hello there', 'Message-ID: <hello@outside.test>', '', 'Hi!', ''].join('\r\n');

describe('initial messages', () => {
  it('composes a message with every option, then a threaded reply', async () => {
    const composed = captureBodies('/scenario-drafts/compose-eml');
    const { user } = await openNew(true);
    const s = await goSection(user, 'Initial messages');
    await user.click(s.getByRole('button', { name: 'Compose a message for Vendor Accounts <vendor@example.test>' }));
    let form = within(s.getByRole('form', { name: /New message in Vendor Accounts/ }));

    // client-side checks before anything is sent
    await user.click(form.getByRole('radio', { name: 'An outside address' }));
    await user.click(form.getByRole('button', { name: 'Add message' }));
    expect(form.getByRole('alert')).toHaveTextContent('Choose who the message is from.');
    await user.type(form.getByLabelText('Sender address'), 'ann@outside.test');
    await user.click(within(form.getByRole('group', { name: 'To' })).getByRole('checkbox', { name: /Vendor Accounts/ }));
    await user.click(form.getByRole('button', { name: 'Add message' }));
    expect(form.getByRole('alert')).toHaveTextContent('Add at least one recipient.');
    await user.type(form.getByLabelText('Other To addresses (comma separated)'), 'x@outside.test; y@outside.test');
    await user.click(form.getByRole('button', { name: 'Add message' }));
    expect(form.getByRole('alert')).toHaveTextContent('Write a text or HTML body.');

    await user.click(within(form.getByRole('group', { name: 'Cc' })).getByRole('checkbox', { name: /Finance Manager/ }));
    await user.type(form.getByLabelText('Other Cc addresses (comma separated)'), 'z@outside.test');
    // earlier messages can be replied to: subject gets "Re: "
    await user.selectOptions(form.getByLabelText('Reply to an earlier message (threading)'), 'fixtures/invoice-001.eml');
    expect(form.getByLabelText('Subject')).toHaveValue('Re: Invoice INV-001');
    await user.type(form.getByLabelText('HTML body (optional)'), '<p>Hi</p>');
    await user.click(form.getByRole('button', { name: 'Preview HTML' }));
    expect(screen.getByTestId('email-html-frame')).toHaveAttribute('sandbox', '');
    await user.click(form.getByRole('button', { name: 'Hide HTML preview' }));
    await user.type(form.getByLabelText(/^Date/), '2026-10-15T10:30');
    const big = new File(['x'], 'big.bin');
    Object.defineProperty(big, 'size', { value: 6 * 1024 * 1024 });
    await user.upload(form.getByLabelText('Attachments'), [new File(['%PDF'], 'a.pdf', { type: 'application/pdf' }), new File(['raw'], 'b'), big]);
    expect(await form.findByText('big.bin does not fit: attachments of one message can total 3.7 MB.')).toBeInTheDocument();
    expect(form.getByRole('list', { name: 'Attached files' })).toHaveTextContent('a.pdf');
    await user.click(form.getByRole('button', { name: 'Remove b' }));
    await user.click(form.getByRole('button', { name: 'Add message' }));

    const items = await s.findAllByTestId('initial-message');
    const added = items.find((i) => i.textContent?.includes('Re: Invoice INV-001')) as HTMLElement;
    expect(added).toHaveTextContent('In reply to');
    expect(added).toHaveTextContent('<invoice-001@vendor.example.test>');
    expect(added).toHaveTextContent('a.pdf');
    expect(composed[0]).toEqual({
      from: 'ann@outside.test',
      to: ['x@outside.test', 'y@outside.test'],
      cc: ['finance@example.test', 'z@outside.test'],
      subject: 'Re: Invoice INV-001',
      text: null,
      html: '<p>Hi</p>',
      date: '2026-10-15T10:30:00Z',
      in_reply_to: '<invoice-001@vendor.example.test>',
      references: ['<invoice-001@vendor.example.test>'],
      attachments: [{ filename: 'a.pdf', content_type: 'application/pdf', content_b64: 'JVBERg==' }],
      fixture_path: 'fixtures/vendor-re-invoice-inv-001.eml',
    });

    // reply to the reply: references carry the chain; an existing "Re:" subject is kept
    await user.click(s.getByRole('button', { name: 'Compose a message for Finance Manager <finance@example.test>' }));
    form = within(s.getByRole('form', { name: /New message in Finance Manager/ }));
    expect(form.getByLabelText('Sender persona')).toHaveValue('vendor@example.test');
    await user.selectOptions(form.getByLabelText('Reply to an earlier message (threading)'), 'fixtures/vendor-re-invoice-inv-001.eml');
    expect(form.getByLabelText('Subject')).toHaveValue('Re: Invoice INV-001');
    await user.type(form.getByLabelText('Plain-text body'), 'Again');
    await user.click(form.getByRole('button', { name: 'Add message' }));
    await waitFor(() => expect(composed).toHaveLength(2));
    expect(composed[1]).toMatchObject({
      in_reply_to: '<compose-1@outside.test>',
      references: ['<invoice-001@vendor.example.test>', '<compose-1@outside.test>'],
      text: 'Again',
      html: null,
      date: '2026-10-15T09:00:00Z', // the scenario's clock start, not the server's "now"
    });
    await waitValid();
  });

  it('caps attachments per message and message files per scenario', async () => {
    const { user } = await openNew(true);
    const s = await goSection(user, 'Initial messages');
    expect(s.getByTestId('fixture-budget')).toHaveTextContent('Message files: 366 B of 28.0 MB per scenario');
    await user.click(s.getByRole('button', { name: /Compose a message for Finance Manager/ }));
    const form = within(s.getByRole('form'));
    const files = Array.from({ length: 21 }, (_, i) => new File(['x'], `Scan ${i}.PDF`));
    await user.upload(form.getByLabelText('Attachments'), files);
    expect(await form.findByText('At most 20 attachments per message.')).toBeInTheDocument();
    expect(form.getByRole('list', { name: 'Attached files' }).children).toHaveLength(20);
    expect(form.getByRole('list', { name: 'Attached files' })).toHaveTextContent('scan-0.pdf');
    await user.upload(form.getByLabelText('Attachments'), new File(['y'], 'one-more.txt'));
    expect(form.getByRole('list', { name: 'Attached files' }).children).toHaveLength(20);

    server.use(
      http.post('*/v1/scenario-drafts/compose-eml', () =>
        HttpResponse.json({ path: 'fixtures/huge.eml', content_b64: 'QQ==', message_id: '<h@x>', size: 29 * 1024 * 1024 }),
      ),
    );
    await user.type(form.getByLabelText('Plain-text body'), 'x');
    await user.click(form.getByRole('button', { name: 'Add message' }));
    expect(await form.findByText(/would take the scenario's message files over 28.0 MB/)).toBeInTheDocument();
    expect(s.getAllByTestId('initial-message')).toHaveLength(1);
    await user.click(form.getByRole('button', { name: 'Cancel' }));

    const eml = (i: number) => {
      const f = new File([EML], `m${i}.eml`);
      Object.defineProperty(f, 'size', { value: Math.floor(4.9 * 1024 * 1024) });
      return f;
    };
    await user.upload(s.getByLabelText(/Upload .eml files for Finance Manager/), [1, 2, 3, 4, 5, 6].map(eml));
    expect(await s.findByText(/m6.eml would take the scenario's message files over 28.0 MB/)).toBeInTheDocument();
    expect(s.getAllByTestId('initial-message')).toHaveLength(6);
  });

  it('shows compose errors from the API and can be cancelled', async () => {
    server.use(http.post('*/v1/scenario-drafts/compose-eml', () => HttpResponse.json({ error: { code: 'INVALID_REQUEST', message: 'bad address', details: {}, request_id: 'r' } }, { status: 400 })));
    const { user } = await openNew(true);
    const s = await goSection(user, 'Initial messages');
    await user.click(s.getByRole('button', { name: /Compose a message for Finance Manager/ }));
    const form = within(s.getByRole('form'));
    await user.type(form.getByLabelText('Plain-text body'), 'x');
    await user.click(form.getByRole('button', { name: 'Add message' }));
    expect(await form.findByText(/bad address/)).toBeInTheDocument();
    await user.click(form.getByRole('button', { name: 'Cancel' }));
    expect(s.queryByRole('form')).toBeNull();
  });

  it('composes with no other personas (outside sender only) and no earlier messages', async () => {
    const { user } = await openNew();
    let s = await goSection(user, 'Personas');
    await user.click(s.getByRole('button', { name: 'Add persona' }));
    s = await goSection(user, 'Mailboxes');
    await user.click(s.getByRole('button', { name: 'Create mailboxes for all personas' }));
    s = await goSection(user, 'Initial messages');
    await user.click(s.getByRole('button', { name: /Compose a message for persona/ }));
    const form = within(s.getByRole('form'));
    expect(form.getByRole('radio', { name: 'An outside address' })).toBeChecked();
    expect(form.getByRole('radio', { name: 'A persona' })).toBeDisabled();
    expect(form.queryByLabelText('Reply to an earlier message (threading)')).toBeNull();
    expect(within(form.getByRole('group', { name: 'To' })).getByText('Nothing to choose from yet.')).toBeInTheDocument();
  });

  it('uploads .eml files, rejects bad ones, and removes messages', async () => {
    const { user } = await openNew(true);
    const s = await goSection(user, 'Initial messages');
    const input = s.getByLabelText('Upload .eml files for Finance Manager <finance@example.test>');
    await user.upload(input, [new File([EML], 'Hello There.eml', { type: 'message/rfc822' }), new File([EML], 'invoice-001.eml')]);
    await waitFor(() => expect(s.getAllByTestId('initial-message')).toHaveLength(3));
    const items = s.getAllByTestId('initial-message');
    expect(items[1]).toHaveTextContent('Hello there');
    expect(items[1]).toHaveTextContent('uploaded .eml');
    expect(items[1]).toHaveTextContent('fixtures/hello-there.eml');
    expect(items[2]).toHaveTextContent('fixtures/invoice-001-2.eml');

    await user.upload(input, new File(['not an email'], 'junk.eml'));
    expect(await s.findByText(/not a valid message/)).toBeInTheDocument();
    const big = new File(['x'], 'huge.eml');
    Object.defineProperty(big, 'size', { value: 6 * 1024 * 1024 });
    await user.upload(input, big);
    expect(await s.findByText('huge.eml is larger than 5.0 MB.')).toBeInTheDocument();

    await user.click(s.getByRole('button', { name: 'Remove message 2: “Hello there”' }));
    expect(s.getAllByTestId('initial-message')).toHaveLength(2);
    await waitValid();
  });

  it('shows missing files, unread files and per-message validation errors', async () => {
    const draft = templateDraft();
    draft.form.mailboxes[0]?.messages.push('fixtures/missing.eml', 'fixtures/unread.eml', 'fixtures/invoice-001.eml');
    draft.fixtures['fixtures/unread.eml'] = textToBase64(EML);
    draft.info['fixtures/invoice-001.eml'] = { ...draft.info['fixtures/invoice-001.eml']!, subject: '', from: null, source: 'uploaded' };
    server.use(http.post('*/v1/scenario-drafts/inspect-eml', () => new Promise<Response>(() => undefined)));
    saveDraft(draftStorageKey('new', ''), draft, 'b');
    const { user } = renderApp('/scenarios/new', sessionFor('controller'));
    await screen.findByTestId('builder-notice');
    const s = await goSection(user, 'Initial messages');
    expect(s.getByText('File missing: upload it again or remove this message.')).toBeInTheDocument();
    expect(s.getByText('Reading message…')).toBeInTheDocument();
    expect(s.getAllByText('(no subject)').length).toBeGreaterThan(0);
    expect(await s.findByText('fixture not found: fixtures/missing.eml', {}, SLOW)).toBeInTheDocument();
    // replying keeps a subject that was already typed; earlier messages without subject/sender are labelled
    await user.click(s.getByRole('button', { name: /Compose a message for Vendor Accounts/ }));
    const form = within(s.getByRole('form'));
    await user.type(form.getByLabelText('Subject'), 'Mine');
    await user.selectOptions(form.getByLabelText('Reply to an earlier message (threading)'), '(no subject) — from unknown');
    expect(form.getByLabelText('Subject')).toHaveValue('Mine');
    await user.selectOptions(form.getByLabelText('Reply to an earlier message (threading)'), 'Not a reply');
    await user.click(form.getByRole('button', { name: 'Cancel' }));
    // the same file listed twice: removing one keeps the file for the other
    await user.click(s.getByRole('button', { name: 'Remove message 1: “fixtures/invoice-001.eml”' }));
    expect(s.getAllByTestId('initial-message')).toHaveLength(3);
    await user.click(s.getByRole('button', { name: 'Remove message 1: “fixtures/missing.eml”' }));
    expect(s.getAllByTestId('initial-message')).toHaveLength(2);
  });
});
