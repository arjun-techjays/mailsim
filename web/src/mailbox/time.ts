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
