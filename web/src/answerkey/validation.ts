// Maps validation issues (server: POST .../answer-keys/validate, and quick client checks) onto
// form paths. Server paths are dotted ("answer_key.assertions.0.check.where.folder"); leak-guard
// findings use "leak:<scenario version>".
import type { ValidationIssue } from '../api/types';
import type { AssertionDraft, KeyDraft } from './model';

export interface Issue {
  /** Form path ("assertions.0.requirement"), or "" for document-level problems. */
  path: string;
  message: string;
  /** Scenario version of a leak-guard finding. */
  leakVersion?: string;
}

export function normalizePath(path: string): string {
  return path
    .replace(/^(body|document)\./, '')
    .replace(/^answer_key(\.|$)/, '')
    .replace(/\[(\d+)\]/g, '.$1');
}

export function fromServer(list: ValidationIssue[]): Issue[] {
  return list.map((i) => {
    const leak = /^leak:(.*)$/.exec(i.path);
    return leak ? { path: '', message: i.message, leakVersion: leak[1] } : { path: normalizePath(i.path), message: i.message };
  });
}

/** Instant checks that need no round trip (the server stays authoritative). */
export function clientIssues(d: KeyDraft): Issue[] {
  const out: Issue[] = [];
  if (!/^\d+$/.test(d.version.trim()) || Number(d.version) < 1) {
    out.push({ path: 'version', message: 'Enter a whole number, 1 or more.' });
  }
  if (!d.allVersions && d.scenarioVersions.length === 0) {
    out.push({ path: 'scenario_versions', message: 'Pick at least one scenario version, or choose "All versions".' });
  }
  const seen = new Map<string, number>();
  d.assertions.forEach((a, i) => {
    if (!a.id.trim()) out.push({ path: `assertions.${i}.id`, message: 'Give the assertion an id.' });
    else if (seen.has(a.id)) out.push({ path: `assertions.${i}.id`, message: `Id "${a.id}" is already used by assertion ${(seen.get(a.id) as number) + 1}.` });
    seen.set(a.id, i);
    if (!a.requirement.trim()) {
      out.push({ path: `assertions.${i}.requirement`, message: 'Describe what this assertion expects.' });
    }
  });
  return out;
}

/** Merges client and server issues, dropping server duplicates of the same path+message. */
export function mergeIssues(client: Issue[], server: Issue[]): Issue[] {
  const seen = new Set(client.map((i) => `${i.path}\u0000${i.message}`));
  return [...client, ...server.filter((i) => !seen.has(`${i.path}\u0000${i.message}`))];
}

const LEAK_FIELDS = ['requirement', 'rubric', 'id'] as const;

/**
 * Fields whose text a leak finding quotes. The leak guard treats requirement, rubric and ids of
 * 8+ characters as secrets; the finding's message contains the leaked text.
 */
export function leakedFields(assertions: AssertionDraft[], leaks: Issue[]): Issue[] {
  const out: Issue[] = [];
  assertions.forEach((a, i) => {
    for (const f of LEAK_FIELDS) {
      const text = a[f].trim();
      if (text.length < 8) continue;
      const hit = leaks.find((l) => l.message.toLowerCase().includes(text.toLowerCase()));
      if (hit) {
        out.push({
          path: `assertions.${i}.${f}`,
          message: `This text also appears in an email the agent can read (scenario v${hit.leakVersion}). Reword it so it does not quote the email.`,
        });
      }
    }
  });
  return out;
}

/** Index of issues by path, for inline display. */
export function byPath(issues: Issue[]): Map<string, string[]> {
  const m = new Map<string, string[]>();
  for (const i of issues) {
    if (i.leakVersion !== undefined) continue;
    m.set(i.path, [...(m.get(i.path) ?? []), i.message]);
  }
  return m;
}
