// A scenario being built: the form, the fixture files it references (path -> base64) and what
// is known about each fixture message (for summaries and "reply to" threading).
// Unsaved drafts are kept in sessionStorage (per tab, cleared when the tab closes).
import type { ScenarioForm } from './model';

export interface FixtureInfo {
  source: 'composed' | 'uploaded' | 'loaded';
  message_id: string | null;
  in_reply_to: string | null;
  /** References header (the thread so far), when known. */
  references?: string[];
  from: string | null;
  to: string[];
  subject: string;
  attachments: string[];
  text_preview: string;
}

export interface Draft {
  form: ScenarioForm;
  fixtures: Record<string, string>;
  info: Record<string, FixtureInfo>;
}

export type BuilderMode = 'new' | 'edit' | 'clone';

export interface StoredDraft {
  draft: Draft;
  /** Document key of the starting point (to tell whether there are unsaved changes). */
  baseline: string;
  /** False when the fixtures were too large to keep (only the form was stored). */
  fixturesKept: boolean;
}

/** Budget for one stored draft (sessionStorage is ~5 MB per origin, shared with the session). */
export const DRAFT_BUDGET_CHARS = 2_000_000;

const PREFIX = 'mailsim.builderDraft:';

export function draftStorageKey(mode: BuilderMode, key: string): string {
  return `${PREFIX}${mode}:${key}`;
}

const FIXTURES = ':fixtures';

/**
 * Saves the draft. The form part is small and written every time; the fixtures are written only
 * when `fixtures` is true (they changed) and dropped when they would exceed the budget.
 * `fixturesKept` is only reported when the fixtures were written.
 */
export function saveDraft(storageKey: string, draft: Draft, baseline: string, fixtures = true): { fixturesKept?: boolean } {
  try {
    sessionStorage.setItem(storageKey, JSON.stringify({ form: draft.form, info: draft.info, baseline }));
    if (!fixtures) return {}; // fixtures unchanged: whatever was kept stays kept
    const text = JSON.stringify(draft.fixtures);
    if (text.length > DRAFT_BUDGET_CHARS) {
      sessionStorage.removeItem(storageKey + FIXTURES);
      return { fixturesKept: false };
    }
    sessionStorage.setItem(storageKey + FIXTURES, text);
    return { fixturesKept: true };
  } catch {
    return { fixturesKept: false }; // quota or storage disabled
  }
}

export function loadDraft(storageKey: string): StoredDraft | null {
  try {
    const raw = sessionStorage.getItem(storageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { form?: Draft['form']; info?: Draft['info']; baseline?: string };
    if (!parsed.form) return null;
    const fixturesText = sessionStorage.getItem(storageKey + FIXTURES);
    return {
      draft: { form: parsed.form, info: parsed.info ?? {}, fixtures: fixturesText ? (JSON.parse(fixturesText) as Record<string, string>) : {} },
      baseline: parsed.baseline ?? '',
      fixturesKept: fixturesText !== null,
    };
  } catch {
    return null;
  }
}

export function clearDraft(storageKey: string): void {
  try {
    sessionStorage.removeItem(storageKey);
    sessionStorage.removeItem(storageKey + FIXTURES);
  } catch {
    /* storage disabled */
  }
}
