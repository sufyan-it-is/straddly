export const THEME_KEY = 'tn_theme_mode';

export const THEMES = ['light', 'dark'] as const;
export type ThemeMode = typeof THEMES[number];

export const normalizeTheme = (value: unknown): ThemeMode => {
  const raw = String(value || '').trim().toLowerCase();
  if (raw === 'dark') return raw;
  return 'light';
};

export const getInitialTheme = (): ThemeMode => {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored && THEMES.includes(stored as ThemeMode)) return stored as ThemeMode;
  } catch {
    // ignore storage errors
  }
  const prefersDark = typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-color-scheme: dark)').matches;
  return prefersDark ? 'dark' : 'light';
};

export const applyTheme = (mode: ThemeMode): void => {
  const next = normalizeTheme(mode);
  document.documentElement.setAttribute('data-theme', next);
  document.documentElement.style.colorScheme = next === 'dark' ? 'dark' : 'light';
};

export const setTheme = (mode: ThemeMode): ThemeMode => {
  const next = normalizeTheme(mode);
  applyTheme(next);
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch {
    // ignore storage errors
  }
  try {
    window.dispatchEvent(new CustomEvent('tn-theme-changed', { detail: { mode: next } }));
  } catch {
    // ignore event errors
  }
  return next;
};
