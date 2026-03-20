import { useState, useEffect, useRef, useCallback } from 'react';
import { apiService } from '../services/apiService';
import { getLotSize, getStrikeInterval } from '../config/tradingConfig';

const DEFAULT_POLL_INTERVAL = 5000; // ms between REST polls when WS not available

/**
 * useAuthoritativeOptionChain
 * Fetches option chain data via REST and streams via WebSocket.
 * Returns { data, loading, error, refresh, getATMStrike, getLotSize, servedExpiry }
 */
export function useAuthoritativeOptionChain(
  underlying,
  expiry,
  {
    autoRefresh = true,
    refreshInterval = 1000,
    pollInterval = DEFAULT_POLL_INTERVAL,
  } = {}
) {
  const [data, setData]           = useState(null);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState(null);
  const [servedExpiry, setServedExpiry] = useState(expiry);

  const wsRef          = useRef(null);
  const timerRef       = useRef(null);
  const mountedRef     = useRef(true);
  const wsConnectedRef = useRef(false);
  const requestSeqRef  = useRef(0);
  const lastStrikesRef = useRef(null); // Track last strikes hash to prevent redundant updates

  // ── ATM drift detection ──────────────────────────────────────────────────
  // Tracks the ATM at the time of the last full calibration (re-fetch).
  // When the live ATM drifts ≥ ATM_DRIFT_THRESHOLD strikes away from that
  // baseline, a fresh REST fetch is triggered so the backend can return a
  // correctly-centred strike window.
  const ATM_DRIFT_THRESHOLD = 7;   // strikes before re-centering
  const baseAtmRef      = useRef(null);  // ATM at last calibration
  const driftFetchingRef = useRef(false); // prevent concurrent drift re-fetches

  // Helper to detect if strikes data has actually changed
  const hasStrikesChanged = useCallback((newStrikes) => {
    if (!newStrikes || !lastStrikesRef.current) return true;
    const newKeys = Object.keys(newStrikes).sort();
    const oldKeys = Object.keys(lastStrikesRef.current).sort();
    if (newKeys.length !== oldKeys.length) return true;
    for (let i = 0; i < newKeys.length; i++) {
      if (newKeys[i] !== oldKeys[i]) return true;
      const newStrike = newStrikes[newKeys[i]];
      const oldStrike = lastStrikesRef.current[oldKeys[i]];
      // Check if CE/PE prices have changed
      if (newStrike?.CE?.ltp !== oldStrike?.CE?.ltp || newStrike?.PE?.ltp !== oldStrike?.PE?.ltp) return true;
    }
    return false;
  }, []);

  // ── REST fetch ──────────────────────────────────────────────────────────
  const fetchData = useCallback(async (ul = underlying, exp = expiry) => {
    if (!ul || !exp) return;
    const requestSeq = ++requestSeqRef.current;
    setLoading(true);
    setError(null);
    try {
      // Request strikes_around=50 to get the full available range from the backend.
      // The frontend already slices to ATM±15 for display, so having 101 strikes ensures
      // the true ATM (derived from live option premiums) is always inside the dataset,
      // even when the backend's cached ATM or the index LTP is stale.
      const result = await apiService.get('/options/live', { underlying: ul, expiry: exp, strikes_around: 50 });
      if (!mountedRef.current) return;
      if (requestSeq !== requestSeqRef.current) return;
      // Backend returns an object: { underlying, expiry, underlying_ltp, lot_size, strike_interval, atm, strikes }
      if (result && typeof result === 'object' && !Array.isArray(result)) {
        const normalized = {
          ...result,
          // keep older UI fields working
          atm_strike: result.atm_strike ?? result.atm ?? null,
        };

        const strikesChanged = hasStrikesChanged(normalized.strikes);
        if (normalized.strikes) {
          lastStrikesRef.current = normalized.strikes;
        }

        setData((prev) => {
          if (!prev) return normalized;

          const metaChanged =
            prev.underlying_ltp !== normalized.underlying_ltp ||
            prev.atm_strike !== normalized.atm_strike ||
            prev.strike_interval !== normalized.strike_interval ||
            prev.lot_size !== normalized.lot_size ||
            prev.underlying !== normalized.underlying ||
            prev.expiry !== normalized.expiry;

          return (strikesChanged || metaChanged) ? normalized : prev;
        });
      } else {
        // Legacy fallback (shouldn't happen): treat as empty.
        setData(null);
      }
      setServedExpiry(exp);
    } catch (e) {
      if (mountedRef.current) setError(e.message || 'Failed to load option chain');
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [underlying, expiry, hasStrikesChanged]);

  // ── WebSocket stream ─────────────────────────────────────────────────────
  const connectWS = useCallback(() => {
    if (!underlying || !expiry) return;
    try {
      const base = apiService.baseURL;
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.host;
      const wsBase = base.startsWith('/')
        ? `${protocol}//${host}${base}`
        : base.replace(/^https?:/, protocol);
      const url = `${wsBase}/options/ws/live?underlying=${underlying}&expiry=${expiry}`;

      if (wsRef.current) wsRef.current.close();
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        wsConnectedRef.current = true;
        // Stop any fallback poller once WS is healthy.
        if (timerRef.current) {
          clearInterval(timerRef.current);
          timerRef.current = null;
        }
      };

      ws.onmessage = (evt) => {
        if (!mountedRef.current) return;
        if (wsRef.current !== ws) return;
        try {
          const msg = JSON.parse(evt.data);
          // Backend WS sends: { type: 'option_chain', strikes: { ... } }
          if (msg && typeof msg === 'object' && msg.strikes) {
            // Only update if strikes data has actually changed
            if (hasStrikesChanged(msg.strikes)) {
              lastStrikesRef.current = msg.strikes;
              setData((prev) => ({
                ...(prev && typeof prev === 'object' ? prev : {}),
                strikes: msg.strikes,
                atm_strike: (prev && prev.atm_strike) || (prev && prev.atm) || null,
              }));
            }
          } else if (msg && typeof msg === 'object' && msg.prices) {
            // ignore unrelated messages
          }
        } catch { /* ignore parse errors */ }
      };

      ws.onclose = () => {
        if (wsRef.current !== ws) return;
        wsConnectedRef.current = false;
        // Fall back to polling
        if (mountedRef.current && timerRef.current === null && autoRefresh) {
          timerRef.current = setInterval(() => fetchData(), pollInterval);
        }
      };
    } catch (e) {
      wsConnectedRef.current = false;
      console.warn('[useAuthoritativeOptionChain] WS error:', e);
      if (autoRefresh) timerRef.current = setInterval(() => fetchData(), pollInterval);
    }
  }, [underlying, expiry, fetchData, hasStrikesChanged, autoRefresh, pollInterval]);

  useEffect(() => {
    mountedRef.current = true;
    setError(null);
    setServedExpiry(expiry);
    if (underlying && expiry) {
      fetchData();
      connectWS();
    }
    return () => {
      mountedRef.current = false;
      wsConnectedRef.current = false;
      clearInterval(timerRef.current);
      timerRef.current = null;
      if (wsRef.current) wsRef.current.close();
    };
  }, [underlying, expiry, fetchData, connectWS, autoRefresh, refreshInterval]);

  // Reset baseline/cache whenever the user switches index or expiry.
  // Automatic drift re-centering is intentionally disabled to prevent list jitter.
  useEffect(() => {
    baseAtmRef.current     = null;
    driftFetchingRef.current = false;
    lastStrikesRef.current = null;
  }, [underlying, expiry]);

  // ── Helpers ───────────────────────────────────────────────────────────────
  const getATMStrike = useCallback((ltp) => {
    const backendAtm = data?.atm_strike ?? data?.atm ?? null;
    if (typeof backendAtm === 'number' && backendAtm > 0) return backendAtm;

    // Last-resort fallback only when backend ATM is unavailable.
    const interval = Number(data?.strike_interval || getStrikeInterval(underlying) || 0);
    const price = Number(ltp || data?.underlying_ltp || 0);
    if (!interval || !price) return null;
    return Math.round(price / interval) * interval;
  }, [data, underlying]);

  const lotSize = getLotSize(underlying);

  const refresh = useCallback(() => fetchData(), [fetchData]);

  /**
   * recalibrate — manually resets the ATM drift baseline to null so that the
   * very next data response re-establishes it from the current live LTP.
   * Use this when you want to force a fresh ATM-centred window on demand.
   */
  const recalibrate = useCallback(() => {
    baseAtmRef.current      = null;
    driftFetchingRef.current = false;
    lastStrikesRef.current = null;
    console.log(`[OptionChain] Manual recalibration triggered for ${underlying}`);
    fetchData();
  }, [fetchData, underlying]);

  return { data, loading, error, refresh, recalibrate, getATMStrike, getLotSize: () => lotSize, servedExpiry };
}

export default useAuthoritativeOptionChain;
