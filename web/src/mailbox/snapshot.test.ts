import { parseSnapshot } from './snapshot';
import { snapshotSource } from './source';

describe('parseSnapshot', () => {
  it('converts engine snapshot rows into canonical messages', () => {
    const snap = parseSnapshot({
      mailboxes: [{ id: 'mb1', email: 'a@x' }, null, 'junk'],
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
    expect(snap.mailboxes).toEqual([{ id: 'mb1', email: 'a@x' }]);
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
    expect(parseSnapshot({})).toEqual({ mailboxes: [], folders: [], messages: [] });
  });
});

describe('snapshotSource', () => {
  it('paginates client-side with opaque cursors', async () => {
    const snap = parseSnapshot({
      folders: [{ id: 'f', mailbox_id: 'mb', name: 'inbox' }],
      messages: Array.from({ length: 5 }, (_, i) => ({ id: `m${i}`, mailbox_id: 'mb', folder_id: 'f', thread_id: 't' })),
    });
    const src = snapshotSource(snap, 'mb', 1);
    const p1 = await src.listMessages(null, null, 2);
    expect(p1.items.map((m) => m.id)).toEqual(['m0', 'm1']);
    const p3 = await src.listMessages('inbox', '4', 2);
    expect(p3).toMatchObject({ next_cursor: null });
    expect(await src.listFolders()).toEqual([{ id: 'f', name: 'inbox', system_type: null }]);
    expect(await src.getThread('t')).toHaveLength(5);
    await expect(src.getMessage('zzz')).rejects.toMatchObject({ code: 'NOT_FOUND', status: 404 });
  });
});
