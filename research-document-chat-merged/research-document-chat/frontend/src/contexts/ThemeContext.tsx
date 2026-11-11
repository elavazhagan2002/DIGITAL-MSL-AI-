import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

type Theme = 'light' | 'dark';

export type ThemePreference = Theme | 'system';

interface ThemeContextValue {
  /** the theme in effect */
  theme: Theme;
  /** what the user chose: light, dark, or follow the system */
  preference: ThemePreference;
  setPreference: (next: ThemePreference) => void;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);
export const THEME_STORAGE_KEY = 'dmsl_theme';

function storedTheme(): Theme | null {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return stored === 'dark' || stored === 'light' ? stored : null;
  } catch {
    return null;
  }
}

function systemTheme(): Theme {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle('dark', theme === 'dark');
  document.documentElement.style.colorScheme = theme;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    const initial = storedTheme() ?? systemTheme();
    if (typeof document !== 'undefined') applyTheme(initial);
    return initial;
  });

  const [preference, setPreferenceState] = useState<ThemePreference>(() => storedTheme() ?? 'system');

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Follow the OS setting until the user chooses a theme with the toggle.
  useEffect(() => {
    if (storedTheme() || !window.matchMedia) return;
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => !storedTheme() && setTheme(mq.matches ? 'dark' : 'light');
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    try {
      if (next === 'system') localStorage.removeItem(THEME_STORAGE_KEY);
      else localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      /* storage unavailable: the choice still applies for this visit */
    }
    setTheme(next === 'system' ? systemTheme() : next);
  }, []);

  const toggle = useCallback(() => {
    setTheme((current) => {
      const next: Theme = current === 'light' ? 'dark' : 'light';
      setPreferenceState(next);
      try {
        localStorage.setItem(THEME_STORAGE_KEY, next);
      } catch {
        /* storage unavailable: the choice still applies for this visit */
      }
      return next;
    });
  }, []);
  const value = useMemo(() => ({ theme, preference, setPreference, toggle }), [theme, preference, setPreference, toggle]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
  return ctx;
}
