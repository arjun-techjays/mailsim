// The unsaved draft of the open tab only (sessionStorage: gone when the tab closes). It is
// removed as soon as the key is published or discarded. Nothing goes to localStorage.
import type { KeyDraft } from './model';

const PREFIX = 'mailsim.answerKeyDraft:';
export const draftKey = (scenario: string, from: number | null) => `${PREFIX}${scenario}:${from ?? 'new'}`;

export function loadDraft(key: string): KeyDraft | null {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return null;
    const d = JSON.parse(raw) as KeyDraft;
    return d && typeof d === 'object' && Array.isArray(d.assertions) && typeof d.version === 'string' ? d : null;
  } catch {
    return null;
  }
}

export function saveDraft(key: string, draft: KeyDraft): void {
  try {
    sessionStorage.setItem(key, JSON.stringify(draft));
  } catch {
    // storage full or disabled: the draft simply is not kept
  }
}

export function clearDraft(key: string): void {
  try {
    sessionStorage.removeItem(key);
  } catch {
    // ignore
  }
}
