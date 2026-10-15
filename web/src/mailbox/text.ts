import type { CanonicalMessage } from '../api/types';

export const subjectOf = (m: { subject?: string | null }) => m.subject || '(no subject)';

/** First ~120 characters of the plain-text body, whitespace collapsed. */
export function snippet(m: Pick<CanonicalMessage, 'body_text'>): string {
  const t = (m.body_text ?? '').replace(/\s+/g, ' ').trim();
  return t.length > 120 ? `${t.slice(0, 117)}…` : t;
}
