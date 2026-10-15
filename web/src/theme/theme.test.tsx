import { screen } from '@testing-library/react';
import { renderApp } from '../test/utils';
import { applyTheme, loadThemePref, saveThemePref, THEME_STORAGE_KEY } from './theme';

afterEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
});

describe('theme preference', () => {
  it('defaults to system and round-trips light/dark through localStorage', () => {
    expect(loadThemePref()).toBe('system');
    saveThemePref('dark');
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
    expect(loadThemePref()).toBe('dark');
    saveThemePref('system');
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
    localStorage.setItem(THEME_STORAGE_KEY, 'neon');
    expect(loadThemePref()).toBe('system');
  });

  it('survives blocked storage', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(loadThemePref()).toBe('system');
    expect(() => saveThemePref('light')).not.toThrow();
  });

  it('survives a missing localStorage', () => {
    const spy = vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(loadThemePref()).toBe('system');
    expect(() => saveThemePref('dark')).not.toThrow();
    spy.mockRestore();
  });

  it('applies data-theme on <html> (none for system)', () => {
    applyTheme('dark');
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    applyTheme('system');
    expect(document.documentElement).not.toHaveAttribute('data-theme');
  });

  it('header toggle switches and remembers the theme', async () => {
    const { user } = renderApp('/concepts');
    const select = screen.getByLabelText('Theme');
    expect(select).toHaveValue('system');
    await user.selectOptions(select, 'dark');
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
    await user.selectOptions(select, 'light');
    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
    await user.selectOptions(select, 'system');
    expect(document.documentElement).not.toHaveAttribute('data-theme');
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });
});
