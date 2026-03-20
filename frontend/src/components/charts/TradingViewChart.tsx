/**
 * TradingView Chart Component
 * Mounts the TradingView charting library with the custom UDF adapter
 */

import React, { useEffect, useRef } from 'react';
import { createUdfDatafeed } from '../../services/tradingviewUdfAdapter';
import { createTvSaveLoadAdapter, createTvSettingsAdapter, getTvInitialSettings } from '../../services/tradingviewStorageAdapter';

declare global {
  interface Window {
    TradingView?: any;
  }
}

interface TradingViewChartProps {
  symbol?: string;
  interval?: string;
  darkMode?: boolean;
  height?: number;
  width?: string;
  onReady?: () => void;
  /** When true (default), restores the last saved chart. Set to false when opening a specific symbol from watchlist. */
  loadLastChart?: boolean;
}

const resolveGlobalDarkMode = (fallback?: boolean): boolean => {
  if (typeof document !== 'undefined') {
    const domTheme = document.documentElement.getAttribute('data-theme');
    if (domTheme === 'dark') return true;
    if (domTheme === 'light') return false;
  }
  if (typeof fallback === 'boolean') return fallback;
  return true;
};

const getThemeOverrides = (isDarkMode: boolean) => ({
  'paneProperties.backgroundType': 'solid',
  'paneProperties.background': isDarkMode ? '#151515' : '#ffffff',
  'paneProperties.vertGridProperties.color': isDarkMode ? '#2b2b2b' : '#e2e8f0',
  'paneProperties.horzGridProperties.color': isDarkMode ? '#2b2b2b' : '#e2e8f0',
  'paneProperties.crossHairProperties.color': isDarkMode ? '#6b7280' : '#94a3b8',
  'scalesProperties.backgroundColor': isDarkMode ? '#151515' : '#ffffff',
  'scalesProperties.textColor': isDarkMode ? '#e5e7eb' : '#334155',
  'scalesProperties.lineColor': isDarkMode ? '#2b2b2b' : '#e2e8f0',
  'mainSeriesProperties.candleStyle.upColor': '#10b981',
  'mainSeriesProperties.candleStyle.downColor': '#ef4444',
  'mainSeriesProperties.candleStyle.drawWick': true,
  'mainSeriesProperties.candleStyle.wickUpColor': '#10b981',
  'mainSeriesProperties.candleStyle.wickDownColor': '#ef4444',
  'mainSeriesProperties.style': 1,
});

export const TradingViewChart: React.FC<TradingViewChartProps> = ({
  symbol = 'Reliance Industries',
  interval = '5',
  darkMode = true,
  height = 600,
  width = '100%',
  onReady,
  loadLastChart = true,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const containerIdRef = useRef<string>(`tv_chart_${Math.random().toString(36).slice(2)}`);
  const activeDarkMode = resolveGlobalDarkMode(darkMode);

  useEffect(() => {
    if (!containerRef.current) return;

    // Create the UDF datafeed adapter
    const udfDatafeed = createUdfDatafeed();

    // Initialize TradingView widget
    const initChart = async () => {
      try {
        // Use the TradingView charting library from public folder
        if (!window.TradingView) {
          console.error('TradingView library not loaded');
          return;
        }

        const authUser = (() => {
          try { return JSON.parse(localStorage.getItem('authUser') || '{}'); } catch { return {}; }
        })();
        const chartUserId = String(authUser?.id || authUser?.mobile || 'anonymous');
        const initialSettings = await getTvInitialSettings();

        const widgetOptions: any = {
          symbol: symbol,
          interval: interval,
          container: containerIdRef.current,
          library_path: '/charting_library/',
          locale: 'en',
          datafeed: udfDatafeed,
          theme: activeDarkMode ? 'Dark' : 'Light',
          timezone: 'Asia/Kolkata',
          autosize: true,
          allow_symbol_change: true,
          header_widget_buttons_mode: 'adaptive',
          loading_screen: {
            backgroundColor: activeDarkMode ? '#151515' : '#f8fafc',
            foregroundColor: activeDarkMode ? '#f59e0b' : '#1d4ed8',
          },
          favorites: {
            intervals: ['1', '3', '5', '15', '30', '60', '1D'],
          },
          client_id: 'straddly',
          user_id: chartUserId,
          save_load_adapter: createTvSaveLoadAdapter(),
          settings_adapter: createTvSettingsAdapter(initialSettings),
          time_frames: [
            { text: '1m', resolution: '1', description: '1 minute' },
            { text: '3m', resolution: '3', description: '3 minutes' },
            { text: '5m', resolution: '5', description: '5 minutes' },
            { text: '15m', resolution: '15', description: '15 minutes' },
            { text: '30m', resolution: '30', description: '30 minutes' },
            { text: '60m', resolution: '60', description: '60 minutes' },
            { text: '1D', resolution: 'D', description: '1 day' },
          ],
          // When loadLastChart is false (explicit watchlist navigation), use the symbol prop directly
          load_last_chart: loadLastChart,
          enabled_features: [
            'study_templates',
            'header_saveload',
            'header_widget',
            'header_resolutions',
            'header_chart_type',
            'header_settings',
            'header_indicators',
            'header_compare',
            'header_undo_redo',
            'header_screenshot',
            'timeframes_toolbar',
            'left_toolbar',
            'keep_left_toolbar_visible_on_small_screens',
            'control_bar',
            'edit_buttons_in_legend',
            'context_menus',
            'study_dialog_search_control',
            'items_favoriting',
            'symbol_search_hot_key',
            'side_toolbar_in_fullscreen_mode',
            'header_in_fullscreen_mode',
          ],
          disabled_features: [
            'volume_force_overlay',
            'create_volume_indicator_by_default',
            'popup_hints',
            'adaptive_logo',
            'show_logo_on_all_charts',
            'branding',
          ],
          custom_css_url: '/tradingview-custom.css',
          height: height,
          width: width,
          studies_overrides: {
            'volume.volume.color.0': 'rgba(60, 120, 216, 0.28)',
            'volume.volume.color.1': 'rgba(242, 54, 69, 0.28)',
          },
          overrides: getThemeOverrides(activeDarkMode),
        };

        chartRef.current = new window.TradingView.widget(widgetOptions);
        chartRef.current.onChartReady(() => {
          try {
            chartRef.current?.applyOverrides?.(getThemeOverrides(activeDarkMode));
          } catch (e) {
            console.warn('Failed to apply TradingView theme overrides:', e);
          }
          onReady?.();
        });
      } catch (error) {
        console.error('Error initializing TradingView chart:', error);
      }
    };

    // Check if TradingView is already loaded
    if (window.TradingView && window.TradingView.widget) {
      initChart();
    } else {
      // Load the TradingView library dynamically
      const script = document.createElement('script');
      script.src = '/charting_library/charting_library.standalone.js';
      script.async = true;
      script.onload = initChart;
      script.onerror = () => console.error('Failed to load TradingView library');
      document.head.appendChild(script);
    }

    return () => {
      // Cleanup chart instance if needed
      if (chartRef.current) {
        try {
          chartRef.current.remove?.();
        } catch (e) {
          console.warn('Error cleaning up chart:', e);
        }
      }
    };
  }, [symbol, interval, activeDarkMode, height, width, onReady]);

  return (
    <div
      ref={containerRef}
      id={containerIdRef.current}
      style={{
        width: width,
        height: height,
        backgroundColor: activeDarkMode ? '#151515' : '#ffffff',
      }}
    />
  );
};

export default TradingViewChart;
