import { parseSnapshot } from './snapshot';

describe('parseSnapshot', () => {
  it('converts engine snapshot rows into canonical messages', () => {
    const snap = parseSnapshot({
      mailboxes: [{ id: 'mb1', email: 'a@x', owner_id: 'p1' }, null, 'junk'],
      personas: [{ id: 'p1', email: 'a@x', role: 'Boss' }, { id: 7 }],
      folders: [{ id: 'f1', mailbox_id: 'mb1', name: 'inbox', system_type: 'inbox' }],
      messages: [
        {
          id: 'm1',
          mailbox_id: 'mb1',
          thread_id: 't1',
          folder_id: 'f1',
          from: { email: 'b@x', name: 'B' },
          to: [{ email: 'a@x' }, 'raw@x'],
          labels: ['L', 3],
          headers: { a: '1' },
          is_draft: true,
          size: 'x',
        },
        { id: 'm2', mailbox_id: 'mb1', folder_id: 'gone', from: 'plain@x', headers: 'bad' },
      ],
      attachments: [
        { id: 'a1', message_id: 'm1', filename: 'f.pdf', mime_type: 'application/pdf', size: 10, inline: true, content_id: 'cid' },
        { id: 'a2', message_id: 'm1', filename: 'g', mime_type: 'x', size: 'big' },
      ],
    });
    expect(snap.mailboxes).toEqual([{ id: 'mb1', email: 'a@x', owner_id: 'p1' }]);
    expect(snap.personas).toEqual([
      { id: 'p1', email: 'a@x', role: 'Boss' },
      { id: '', email: '', role: '' },
    ]);
    const [m1, m2] = snap.messages;
    expect(m1).toMatchObject({
      folder: 'inbox',
      from: { email: 'b@x', name: 'B' },
      to: [{ email: 'a@x', name: null }, { email: 'raw@x' }],
      labels: ['L'],
      is_draft: true,
      body_text: null,
    });
    expect(m1?.attachments).toHaveLength(2);
    expect(m1?.attachments?.[0]).toMatchObject({ inline: true, content_id: 'cid', size: 10 });
    expect(m1?.attachments?.[1]).toMatchObject({ inline: false, content_id: null, size: 0 });
    expect(m2).toMatchObject({ folder: 'gone', from: { email: 'plain@x' }, headers: {}, attachments: [] });
  });

  it('tolerates an empty snapshot', () => {
    expect(parseSnapshot({})).toEqual({ mailboxes: [], personas: [], folders: [], messages: [] });
  });
});
