// datetime-local inputs <-> ISO 8601 UTC strings (`2026-10-15T09:00:00Z`). The builder labels
// these inputs "UTC": the value typed is taken as UTC, never converted from local time.

const ISO_UTC = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(:\d{2})?(?:\.\d+)?Z$/;

/** Value for a datetime-local input, or '' when `iso` is not a UTC timestamp. */
export function toIsoInput(iso: string): string {
  const m = ISO_UTC.exec(iso);
  if (!m) return '';
  return m[2] && m[2] !== ':00' ? `${m[1]}${m[2]}` : (m[1] as string);
}

/** ISO UTC timestamp for a datetime-local value ('' stays ''). */
export function fromIsoInput(value: string): string {
  if (!value) return '';
  return value.length === 16 ? `${value}:00Z` : `${value}Z`;
}
