import type { CanonicalMessage } from '../api/types';

// Small display helpers for mail (subjects, snippets, virtual time).
/** "2026-03-02T09:01:00Z" -> "2 Mar 2026, 09:01 UTC" (virtual simulator time, always UTC). */
export function formatVirtual(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCDate()} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()}, ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}

/** "2 Mar, 09:01" (compact list form; the full value is in the element's title). */
export function formatVirtualShort(iso: string): string {
  const full = formatVirtual(iso);
  const m = /^(\d+ \w+) \d+, (\d\d:\d\d) UTC$/.exec(full);
  return m ? `${m[1]}, ${m[2]}` : full;
}


export const subjectOf = (m: { subject?: string | null }) => m.subject || '(no subject)';

/** First ~120 characters of the plain-text body, whitespace collapsed. */
export function snippet(m: Pick<CanonicalMessage, 'body_text'>): string {
  const t = (m.body_text ?? '').replace(/\s+/g, ' ').trim();
  return t.length > 120 ? `${t.slice(0, 117)}…` : t;
}

const when = (m: Pick<CanonicalMessage, 'received_at' | 'created_at'>) => m.received_at ?? m.created_at;

type Sortable = Pick<CanonicalMessage, 'id' | 'received_at' | 'created_at' | 'message_id_header' | 'in_reply_to' | 'references'>;

/**
 * Oldest first, stable: by time, then thread ancestry (a reply after the message it answers, via
 * In-Reply-To / References, also when both carry the same virtual timestamp), then id.
 */
export function chronological<T extends Sortable>(list: readonly T[]): T[] {
  const byHeader = new Map<string, T>();
  for (const m of list) if (m.message_id_header) byHeader.set(m.message_id_header, m);
  const depths = new Map<string, number>();
  const depth = (m: T): number => {
    const known = depths.get(m.id);
    if (known !== undefined) return known;
    depths.set(m.id, 0); // cycle guard
    const parentHeader = m.in_reply_to ?? m.references?.[m.references.length - 1] ?? null;
    const parent = parentHeader ? byHeader.get(parentHeader) : undefined;
    // Ancestors outside the list still count (References lists the whole chain).
    const d = parent && parent.id !== m.id ? depth(parent) + 1 : Math.max(m.references?.length ?? 0, m.in_reply_to ? 1 : 0);
    depths.set(m.id, d);
    return d;
  };
  return [...list].sort(
    (a, b) => when(a).localeCompare(when(b)) || depth(a) - depth(b) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
  );
}

/** Newest first: the exact reverse of `chronological`. */
export function newestFirst<T extends Sortable>(list: readonly T[]): T[] {
  return chronological(list).reverse();
}
