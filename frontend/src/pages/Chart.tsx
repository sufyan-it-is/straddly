/**
 * TradingView Chart Page
 * Full-screen chart page driven by TradingView's native top toolbar controls.
 */

import React, { useEffect, useState } from 'react';
import TradingViewChart from '../components/charts/TradingViewChart';
import { getInitialTheme, THEME_KEY, type ThemeMode } from '../utils/theme';

// Read theme from the already-applied data-theme DOM attribute first (authoritative),
// then fall back to localStorage/system-preference.
const getDOMTheme = (): ThemeMode => {
  const attr = document.documentElement.getAttribute('data-theme');
  if (attr === 'dark') return 'dark';
  if (attr === 'light') return 'light';
  return getInitialTheme();
};

export default function ChartPage() {
  const [selectedSymbol] = useState<string>('Reliance Industries');
  const [selectedInterval] = useState<string>('15');
  const [themeMode, setThemeMode] = useState<ThemeMode>(getDOMTheme());
  const [viewportHeight, setViewportHeight] = useState<number>(window.innerHeight);

  // Keep chart height in sync with screen rotation/resizing on mobile webviews.
  useEffect(() => {
    const updateViewportHeight = () => setViewportHeight(window.innerHeight);
    updateViewportHeight();

    window.addEventListener('resize', updateViewportHeight);
    window.addEventListener('orientationchange', updateViewportHeight);

    return () => {
      window.removeEventListener('resize', updateViewportHeight);
      window.removeEventListener('orientationchange', updateViewportHeight);
    };
  }, []);

  // Listen for theme changes from other tabs/components
  useEffect(() => {
    const handleStorageChange = (e: StorageEvent) => {
      if (e.key === THEME_KEY && e.newValue) {
        setThemeMode(e.newValue as ThemeMode);
      }
    };
    const handleThemeChange = (e: Event) => {
      const custom = e as CustomEvent<{ mode?: ThemeMode }>;
      const mode = custom?.detail?.mode;
      if (mode) setThemeMode(mode);
    };
    window.addEventListener('storage', handleStorageChange);
    window.addEventListener('tn-theme-changed', handleThemeChange as EventListener);

    const observer = new MutationObserver(() => {
      const mode = document.documentElement.getAttribute('data-theme');
      if (mode === 'dark' || mode === 'light') {
        setThemeMode(mode as ThemeMode);
      }
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });

    return () => {
      window.removeEventListener('storage', handleStorageChange);
      window.removeEventListener('tn-theme-changed', handleThemeChange as EventListener);
      observer.disconnect();
    };
  }, []);

  return (
    <div className={`min-h-screen transition-colors duration-300 ${
      themeMode === 'dark' 
        ? 'bg-slate-900 text-slate-100' 
        : 'bg-slate-50 text-slate-900'
    }`}>
      <div className="px-2 pb-2 pt-2">
        <div className={`rounded-lg shadow-lg overflow-hidden border transition-colors duration-300 min-h-[calc(100vh-92px)] ${
          themeMode === 'dark'
            ? 'bg-slate-800 border-slate-700'
            : 'bg-white border-slate-200'
        }`}>
          <TradingViewChart
            key={`${selectedSymbol}:${selectedInterval}:${themeMode}:${viewportHeight}`}
            symbol={selectedSymbol}
            interval={selectedInterval}
            darkMode={themeMode === 'dark'}
            height={Math.max(viewportHeight - 100, 360)}
            width="100%"
          />
        </div>
      </div>
    </div>
  );
}
