import { useId, useState } from 'react';
import { applyTheme, loadThemePref, saveThemePref, THEME_PREFS, type ThemePref } from './theme';

const LABELS: Record<ThemePref, string> = { system: 'System', light: 'Light', dark: 'Dark' };

/** Theme picker for the header: light, dark or follow the operating system. */
export function ThemeToggle() {
  const id = useId();
  const [pref, setPref] = useState<ThemePref>(loadThemePref);
  const change = (next: ThemePref) => {
    setPref(next);
    saveThemePref(next);
    applyTheme(next);
  };
  return (
    <div className="theme-toggle">
      <label htmlFor={id}>Theme</label>
      <select id={id} value={pref} onChange={(e) => change(e.target.value as ThemePref)} data-testid="theme-select">
        {THEME_PREFS.map((p) => (
          <option key={p} value={p}>
            {LABELS[p]}
          </option>
        ))}
      </select>
    </div>
  );
}
