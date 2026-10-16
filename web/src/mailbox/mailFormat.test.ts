import { chronological, newestFirst } from './mailFormat';

const msg = (id: string, at: string, header: string, inReplyTo: string | null = null, references: string[] = []) => ({
  id,
  created_at: at,
  received_at: null,
  message_id_header: header,
  in_reply_to: inReplyTo,
  references,
});

describe('chronological (UI-5)', () => {
  const T = '2026-03-02T09:00:00Z';
  it('puts a reply after its parent when both have the same timestamp, whatever the ids', () => {
    const parent = msg('zzz', T, '<p@x>');
    const reply = msg('aaa', T, '<r@x>', '<p@x>', ['<p@x>']);
    const replyToReply = msg('mmm', T, '<rr@x>', '<r@x>', ['<p@x>', '<r@x>']);
    expect(chronological([replyToReply, reply, parent]).map((m) => m.id)).toEqual(['zzz', 'aaa', 'mmm']);
    expect(newestFirst([parent, replyToReply, reply]).map((m) => m.id)).toEqual(['mmm', 'aaa', 'zzz']);
  });

  it('orders by time first, then ancestry (also from References alone), then id', () => {
    const early = msg('b', '2026-03-02T08:00:00Z', '<e@x>');
    const lateParent = msg('c', '2026-03-02T10:00:00Z', '<lp@x>');
    const outsideReply = msg('a', T, '<o@x>', null, ['<missing@x>']); // ancestor not in the list
    const root = msg('d', T, '<d@x>');
    const sameRootA = msg('a2', T, '');
    expect(chronological([lateParent, outsideReply, root, early, sameRootA]).map((m) => m.id)).toEqual(['b', 'a2', 'd', 'a', 'c']);
  });

  it('tolerates messages without References and duplicate entries', () => {
    const bare = { ...msg('x', T, '<x@x>'), references: undefined } as unknown as ReturnType<typeof msg>;
    const reply = { ...msg('y', T, '<y@x>', '<gone@x>'), references: undefined } as unknown as ReturnType<typeof msg>;
    expect(chronological([reply, bare, bare]).map((m) => m.id)).toEqual(['x', 'x', 'y']);
  });

  it('compares instants, not strings (offsets and fractions), falling back to text', () => {
    const utc = msg('b', '2026-03-02T09:30:00Z', '<b@x>');
    const offset = msg('a', '2026-03-02T10:00:00+01:00', '<a@x>'); // 09:00Z: earlier
    const frac = msg('c', '2026-03-02T09:30:00.500Z', '<c@x>');
    expect(chronological([utc, frac, offset]).map((m) => m.id)).toEqual(['a', 'b', 'c']);
    expect(chronological([msg('y', 'zz', '<y@x>'), msg('x', 'aa', '<x@x>')]).map((m) => m.id)).toEqual(['x', 'y']);
  });

  it('survives reply cycles and self references', () => {
    const a = msg('a', T, '<a@x>', '<b@x>');
    const b = msg('b', T, '<b@x>', '<a@x>');
    const self = msg('s', T, '<s@x>', '<s@x>');
    expect(chronological([a, b, self]).map((m) => m.id).sort()).toEqual(['a', 'b', 's']);
  });
});
