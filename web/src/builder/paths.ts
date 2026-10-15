// Validation issues carry dotted paths into the document (`scenario.personas.1.email`; the
// server's reference checks use `scenario.personas[1].email`). Form fields register under the
// same normalized path, so an issue can be shown next to its field and focused from the summary.
import type { ValidationIssue } from '../api/types';

export type SectionId = 'basics' | 'organisation' | 'personas' | 'mailboxes' | 'messages' | 'events' | 'agent' | 'limits';

export const SECTIONS: { id: SectionId; label: string }[] = [
  { id: 'basics', label: 'Basics' },
  { id: 'organisation', label: 'Organisation' },
  { id: 'personas', label: 'Personas' },
  { id: 'mailboxes', label: 'Mailboxes' },
  { id: 'messages', label: 'Initial messages' },
  { id: 'events', label: 'Events' },
  { id: 'agent', label: 'Agent' },
  { id: 'limits', label: 'Limits & environment' },
];

/** `scenario.personas[1].email` / `body.document.scenario...` -> `scenario.personas.1.email`. */
export function normalizePath(path: string): string {
  return path
    .replace(/\[(\d+)\]/g, '.$1')
    .replace(/^(body\.)?(document\.)?/, '')
    .replace(/^\.+/, '');
}

/** DOM id of the field (or group) for a normalized path. */
export function fieldId(path: string): string {
  return `fld-${path.replace(/[^A-Za-z0-9_-]+/g, '-')}`;
}

export function sectionFor(path: string): SectionId {
  const p = normalizePath(path);
  const rest = p.startsWith('scenario.') ? p.slice('scenario.'.length) : p;
  const head = rest.split('.')[0] as string;
  if (/^mailboxes\.\d+\.initial_messages/.test(rest) || head === 'fixtures') return 'messages';
  if (rest.startsWith('agent.mailbox')) return 'mailboxes';
  switch (head) {
    case 'organization':
      return 'organisation';
    case 'personas':
    case 'relationships':
    case 'projects':
      return 'personas';
    case 'mailboxes':
      return 'mailboxes';
    case 'events':
      return 'events';
    case 'agent':
      return 'agent';
    case 'environment':
    case 'evaluation':
      return 'limits';
    default:
      return 'basics';
  }
}

export interface IndexedIssue extends ValidationIssue {
  /** Normalized path. */
  key: string;
  section: SectionId;
}

export function indexIssues(issues: readonly ValidationIssue[]): IndexedIssue[] {
  return issues.map((i) => ({ ...i, key: normalizePath(i.path), section: sectionFor(i.path) }));
}

/** The closest element registered for `path` or one of its ancestors (null if none). */
export function findTarget(path: string): HTMLElement | null {
  const parts = normalizePath(path).split('.');
  while (parts.length > 0) {
    const el = document.getElementById(fieldId(parts.join('.')));
    if (el) return el;
    parts.pop();
  }
  return null;
}
