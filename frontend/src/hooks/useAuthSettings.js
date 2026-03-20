// Custom hook for DhanHQ authentication settings
import { useState, useEffect } from 'react';
import { apiService } from '../services/apiService';

const LOCAL_CRED_CACHE_KEY = 'dhanCredViewCache';

export const useAuthSettings = () => {
  const [localSettings, setLocalSettings] = useState({
    authMode: 'AUTO_TOTP', // AUTO_TOTP | STATIC_IP | DAILY_MANUAL
    clientId: '',
    accessToken: '',
    apiKey: '',
    clientSecret: '',
    dhanPin: '',
    dhanTotpSecret: '',
    hasSavedTotp: false,
    hasSavedStatic: false,
    connected: false,
    lastAuthTime: null,
    authStatus: 'disconnected',
    wsUrl: 'wss://api-feed.dhan.co?version=2&token=...&clientId=...&authType=2',
  });

  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);

  const toUiMode = (mode) => {
    const raw = String(mode || '').toLowerCase().trim();
    if (raw === 'static_ip') return 'STATIC_IP';
    if (raw === 'manual') return 'DAILY_MANUAL';
    return 'AUTO_TOTP';
  };

  const toApiMode = (mode) => {
    const raw = String(mode || '').toUpperCase().trim();
    if (raw === 'STATIC_IP') return 'static_ip';
    if (raw === 'DAILY_TOKEN' || raw === 'DAILY_MANUAL' || raw === 'MANUAL') return 'manual';
    return 'auto_totp';
  };

  const withTimeout = (promise, ms = 12000) => {
    let timeoutId;
    const timeoutPromise = new Promise((_, reject) => {
      timeoutId = setTimeout(() => reject(new Error('Request timeout')), ms);
    });
    return Promise.race([promise, timeoutPromise]).finally(() => clearTimeout(timeoutId));
  };

  const extractCredentialData = (result) => {
    if (!result || typeof result !== 'object') return {};
    if (result.data && typeof result.data === 'object' && !Array.isArray(result.data)) {
      if (
        'auth_mode' in result.data ||
        'client_id' in result.data ||
        'client_id_prefix' in result.data ||
        'has_token' in result.data
      ) {
        return result.data;
      }
      if (
        result.data.data &&
        typeof result.data.data === 'object' &&
        (
          'auth_mode' in result.data.data ||
          'client_id' in result.data.data ||
          'client_id_prefix' in result.data.data ||
          'has_token' in result.data.data
        )
      ) {
        return result.data.data;
      }
    }
    return result;
  };

  // Load settings from backend
  const loadSavedSettings = async () => {
    try {
      const result = await withTimeout(apiService.get('/admin/credentials/active'));
      const data = extractCredentialData(result);
      const hasPersistedToken = Boolean(
        data?.has_token ||
        data?.has_auth_token ||
        data?.has_daily_token ||
        data?.token_masked
      );

      if (data?.client_id || data?.client_id_prefix || hasPersistedToken) {
        const next = {
          authMode: toUiMode(data.effective_mode || data.auth_mode),
          clientId: data.client_id || (data.client_id_prefix ? `${data.client_id_prefix}****` : ''),
          accessToken: hasPersistedToken ? (data.token_masked || '****************') : '',
          apiKey: '',
          clientSecret: '',
          dhanPin: data?.totp_configured ? (data?.pin_masked || '******') : '',
          dhanTotpSecret: data?.totp_configured ? (data?.totp_secret_masked || '********') : '',
          hasSavedTotp: Boolean(data?.totp_configured),
          hasSavedStatic: Boolean(data?.static_configured),
          connected: hasPersistedToken,
          lastAuthTime: data.last_updated,
          authStatus: hasPersistedToken ? 'connected' : 'disconnected',
          wsUrl: 'wss://api-feed.dhan.co?version=2&token=...&clientId=...&authType=2',
          _cachedCredentials: {
            DAILY_TOKEN: data,
            STATIC_IP: null
          }
        };
        try {
          localStorage.setItem(
            LOCAL_CRED_CACHE_KEY,
            JSON.stringify({
              authMode: next.authMode,
              clientId: next.clientId,
              hasToken: hasPersistedToken,
              lastAuthTime: next.lastAuthTime,
            })
          );
        } catch (_e) {
          // ignore cache write errors
        }
        return next;
      }
    } catch (e) {
      console.warn('Failed to load credentials via apiService', e);
    }

    // Fallback: try localStorage cache
    try {
      const raw = localStorage.getItem(LOCAL_CRED_CACHE_KEY);
      if (raw) {
        const cached = JSON.parse(raw);
        if (cached && cached.clientId) {
          return {
            authMode: cached.authMode || 'AUTO_TOTP',
            clientId: cached.clientId || '',
            accessToken: cached.hasToken ? '****************' : '',
            apiKey: '',
            clientSecret: '',
            dhanPin: '',
            dhanTotpSecret: '',
            hasSavedTotp: false,
            hasSavedStatic: false,
            connected: !!cached.hasToken,
            lastAuthTime: cached.lastAuthTime || null,
            authStatus: cached.hasToken ? 'connected' : 'disconnected',
            wsUrl: 'wss://api-feed.dhan.co?version=2&token=...&clientId=...&authType=2',
            _cachedCredentials: {
              DAILY_TOKEN: null,
              STATIC_IP: null
            }
          };
        }
      }
    } catch (_e) {
      // ignore cache read errors
    }

    return null;
  };

  // Save settings
  const saveSettings = async () => {
    setIsSaving(true);
    try {
      if (!localSettings.clientId) {
        throw new Error('Client ID is required');
      }

      if (localSettings.authMode === 'AUTO_TOTP') {
        const pin = (localSettings.dhanPin || '').trim();
        const totpSecret = (localSettings.dhanTotpSecret || '').trim();
        const pinMasked = /^([*\u2022])+$/u.test(pin);
        const totpMasked = /^([*\u2022])+$/u.test(totpSecret);
        const providedPin = pin.length > 0 && !pinMasked;
        const providedTotp = totpSecret.length > 0 && !totpMasked;
        if (providedPin !== providedTotp) {
          throw new Error('Enter both DHAN PIN and TOTP Secret together for AUTO_TOTP mode');
        }
        if (!localSettings.hasSavedTotp && !(providedPin && providedTotp)) {
          throw new Error('DHAN PIN and TOTP Secret are required for AUTO_TOTP mode');
        }
      }

      if (localSettings.authMode === 'STATIC_IP') {
        const apiKey = (localSettings.apiKey || '').trim();
        const clientSecret = (localSettings.clientSecret || '').trim();
        if (!localSettings.hasSavedStatic && (!apiKey || !clientSecret)) {
          throw new Error('API Key and Client Secret are required for Static IP mode');
        }
      }

      if (localSettings.authMode === 'DAILY_MANUAL') {
        const token = (localSettings.accessToken || '').trim();
        if (!token || token.length < 10 || /^([*\u2022])+$/u.test(token)) {
          throw new Error('Paste a valid daily access token for Daily Manual mode');
        }
      }

      const payload = {
        client_id: localSettings.clientId,
        access_token: localSettings.accessToken,
        api_key: localSettings.apiKey || '',
        secret_api: localSettings.clientSecret || '',
        dhan_pin: localSettings.dhanPin || '',
        dhan_totp_secret: localSettings.dhanTotpSecret || '',
        daily_token: localSettings.accessToken,
        auth_mode: toApiMode(localSettings.authMode)
      };

      const response = await withTimeout(apiService.post('/admin/credentials/save', payload), 15000);
      if (!response || response.success !== true || response.error) {
        throw new Error((response && response.error) || 'Failed to save credentials');
      }

      // Clear cached credentials to ensure we fetch latest values
      try {
        apiService.clearCacheEntry?.('/admin/credentials/active', {});
      } catch (e) {
        // ignore if cache clear not available
      }

      try {
        const refreshed = await loadSavedSettings();
        if (refreshed && (refreshed.clientId || refreshed.accessToken || refreshed.connected)) {
          setLocalSettings(refreshed);
        } else {
          setLocalSettings((prev) => ({
            ...prev,
            connected: true,
            authStatus: 'connected',
            lastAuthTime: new Date().toISOString(),
          }));
        }
      } catch (_refreshError) {
        setLocalSettings((prev) => ({
          ...prev,
          connected: true,
          authStatus: 'connected',
          lastAuthTime: new Date().toISOString(),
        }));
      }

      setSaved(true);
      setTimeout(() => setSaved(false), 3000);

    } catch (error) {
      console.error('🔐 SaveSettings error:', error);
      setSaved(false);
      throw error;
    } finally {
      setIsSaving(false);
    }
  };

  // Switch authentication mode
  const switchMode = async (newMode, options = {}) => {
    const force = Boolean(options?.force);
    const dailyToken = options?.dailyToken || localSettings.accessToken || '';
    try {
      const res = await apiService.post('/admin/auth-mode/switch', {
        auth_mode: toApiMode(newMode),
        force,
        daily_token: dailyToken,
      });
      if (res && !res.error) {
        try {
          apiService.clearCacheEntry?.('/admin/credentials/active', {});
        } catch (e) {
          // ignore
        }
        const refreshed = await loadSavedSettings();
        if (refreshed) setLocalSettings(refreshed);
      } else {
        setLocalSettings(prev => ({ ...prev, authMode: newMode }));
      }
    } catch (error) {
      console.error('Error switching mode:', error);
      throw error;
    }
  };

  // Initialize on mount
  useEffect(() => {
    const initializeSettings = async () => {
      try {
        const settings = await loadSavedSettings();
        if (settings && (settings.clientId || settings.accessToken || settings.connected)) {
          setLocalSettings(settings);
        }
      } catch (error) {
        console.error('Error initializing settings:', error);
      } finally {
        setLoading(false);
      }
    };

    initializeSettings();
  }, []);

  return {
    localSettings,
    setLocalSettings,
    saved,
    setSaved,
    loading,
    isSaving,
    setLoading,
    saveSettings,
    switchMode,
  };
};
