import type { AssertionDraft } from './model';
import { fieldId } from './model';

/** "assertions.1.check.where.folder" -> "Assertion 2 “id”: check › where › folder". */
export function describePath(path: string, assertions: AssertionDraft[]): string {
  if (!path || path === '<root>') return 'Whole answer key';
  if (path === '<source>') return 'YAML text';
  if (/^line \d+$/.test(path)) return `YAML ${path}`;
  const m = /^assertions\.(\d+)(?:\.(.*))?$/.exec(path);
  if (!m) return path.replace(/\./g, ' › ');
  const a = assertions[Number(m[1])];
  const head = `Assertion ${Number(m[1]) + 1}${a?.id ? ` “${a.id}”` : ''}`;
  return m[2] ? `${head}: ${m[2].replace(/\./g, ' › ')}` : head;
}

/** DOM ids to try, most specific first: the field, its ancestors (and card headings), then fallbacks. */
export function focusCandidates(path: string): string[] {
  const parts = path ? path.split('.') : [];
  const ids: string[] = [];
  for (let n = parts.length; n > 0; n -= 1) {
    const id = fieldId(parts.slice(0, n).join('.'));
    ids.push(id, `${id}-h`);
  }
  return [...ids, 'ak-yaml', 'ak-version'];
}

/** Focuses the control for `path`, or its closest rendered ancestor. */
export function focusPath(path: string): void {
  for (const id of focusCandidates(path)) {
    const el = document.getElementById(id);
    if (el) {
      el.focus();
      return;
    }
  }
}
