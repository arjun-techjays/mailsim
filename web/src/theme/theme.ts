// Colour theme preference: light, dark or follow the OS (`system`, the default).
// The preference is a per-browser convenience, so it lives in localStorage. It is the ONLY
// thing the dashboard keeps there: keys and session data stay in memory/sessionStorage.
// Every access is wrapped in try/catch (private windows, blocked storage).

export type ThemePref = 'light' | 'dark' | 'system';
export const THEME_STORAGE_KEY = 'mailsim.theme';
export const THEME_PREFS: readonly ThemePref[] = ['system', 'light', 'dark'];

function prefStore(): Storage | null {
  try {
    return window.localStorage; // theme preference only (see the header comment)
  } catch {
    return null;
  }
}

export function loadThemePref(): ThemePref {
  try {
    const v = prefStore()?.getItem(THEME_STORAGE_KEY);
    return v === 'light' || v === 'dark' ? v : 'system';
  } catch {
    return 'system';
  }
}

export function saveThemePref(pref: ThemePref): void {
  try {
    const s = prefStore();
    if (pref === 'system') s?.removeItem(THEME_STORAGE_KEY);
    else s?.setItem(THEME_STORAGE_KEY, pref);
  } catch {
    // storage disabled or full: the choice applies to this page only
  }
}

/** `data-theme` on <html> overrides prefers-color-scheme; no attribute = follow the OS. */
export function applyTheme(pref: ThemePref, root: HTMLElement = document.documentElement): void {
  if (pref === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', pref);
}
