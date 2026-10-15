// A scenario being built: the form, the fixture files it references (path -> base64) and what
// is known about each fixture message (for summaries and "reply to" threading).
// Unsaved drafts are kept in sessionStorage (per tab, cleared when the tab closes).
import type { ScenarioForm } from './model';

export interface FixtureInfo {
  source: 'composed' | 'uploaded' | 'loaded';
  message_id: string | null;
  in_reply_to: string | null;
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

/** Saves the draft; drops the fixtures if the whole draft would exceed the budget. */
export function saveDraft(storageKey: string, draft: Draft, baseline: string): { fixturesKept: boolean } {
  const full: StoredDraft = { draft, baseline, fixturesKept: true };
  let text = JSON.stringify(full);
  let fixturesKept = true;
  if (text.length > DRAFT_BUDGET_CHARS) {
    fixturesKept = false;
    text = JSON.stringify({ draft: { ...draft, fixtures: {} }, baseline, fixturesKept } satisfies StoredDraft);
  }
  try {
    sessionStorage.setItem(storageKey, text);
  } catch {
    return { fixturesKept: false }; // quota or storage disabled: nothing kept
  }
  return { fixturesKept };
}

export function loadDraft(storageKey: string): StoredDraft | null {
  try {
    const raw = sessionStorage.getItem(storageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredDraft;
    return parsed && parsed.draft && parsed.draft.form ? parsed : null;
  } catch {
    return null;
  }
}

export function clearDraft(storageKey: string): void {
  try {
    sessionStorage.removeItem(storageKey);
  } catch {
    /* storage disabled */
  }
}
