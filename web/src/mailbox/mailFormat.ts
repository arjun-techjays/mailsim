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
