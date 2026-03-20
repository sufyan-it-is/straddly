/**
 * Custom UDF Adapter for TradingView Widget
 * Bridges TradingView's UDF protocol to Straddly backend chart API.
 * 
 * The adapter translates UDF requests to existing:
 * - /chart/instruments (symbol search/config)
 * - /chart/history (historical bars)
 * - /chart/quotes (current prices)
 */

import { apiService } from './apiService';

// UDF-compatible symbol metadata structure
interface UDFSymbol {
  name: string;
  ticker?: string;
  full_name?: string;
  exchange: string;
  listed_exchange: string;
  timezone: string;
  session: string;
  session_regular: string;
  session_premarket?: string;
  session_postmarket?: string;
  minmov: number;
  pricescale: number;
  type: string;
  has_intraday?: boolean;
  has_daily?: boolean;
  has_weekly_and_monthly?: boolean;
  has_no_volume?: boolean;
  visible_plots_set?: string;
  supported_resolutions: string[];
  volume_precision: number;
  data_status: 'streaming' | 'endofday' | 'pulsed' | 'delayed_streaming';
  description?: string;
}

// UDF-compatible bar/candle structure
interface UDFBar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

// Configuration response
interface UDFConfig {
  supports_marks: boolean;
  supports_timescale_marks: boolean;
  supports_time: boolean;
  supports_search: boolean;
  supports_group_request: boolean;
  supports_aggregate: boolean;
  supports_seconds: boolean;
  supports_haozhu: boolean;
  supported_resolutions: string[];
}

/**
 * UDF Config endpoint response
 */
export const getUdfConfig = (): UDFConfig => {
  return {
    supports_marks: false,
    supports_timescale_marks: false,
    supports_time: true,
    supports_search: true,
    supports_group_request: false,
    supports_aggregate: false,
    supports_seconds: false,
    supports_haozhu: false,
    supported_resolutions: ['1', '3', '5', '15', '25', '30', '60', '75', 'D'],
  };
};

const normalizeResolutions = (intervals: unknown): string[] => {
  const fallback = ['1', '3', '5', '15', '25', '30', '60', '75', 'D'];
  if (!Array.isArray(intervals)) return fallback;

  const supported = new Set(['1', '3', '5', '15', '25', '30', '60', '75', 'D', 'W', 'M']);
  const out: string[] = [];

  for (const value of intervals) {
    const raw = String(value || '').trim().toUpperCase();
    if (!raw) continue;
    const normalized = raw.endsWith('M') && /^\d+M$/.test(raw)
      ? raw.slice(0, -1)
      : raw;
    if (!supported.has(normalized)) continue;
    if (!out.includes(normalized)) out.push(normalized);
  }

  return out.length > 0 ? out : fallback;
};

const normalizeResolution = (resolution: string): string => {
  const raw = String(resolution || '').trim().toUpperCase();
  if (!raw) return '5';

  if (raw === '1D') return 'D';
  if (raw === '1W') return 'W';
  if (raw === '1M') return 'M';

  return raw;
};

/**
 * UDF Symbols endpoint - fetch symbol metadata and config
 */
export const resolveSymbol = async (
  symbolName: string
): Promise<UDFSymbol | Record<string, never>> => {
  try {
    // Query the backend for instrument metadata
    const response = await apiService.get(`/chart/instruments`, {
      query: symbolName,
      limit: 1,
    });

    const list = Array.isArray(response?.data)
      ? response.data
      : (Array.isArray(response) ? response : []);

    if (!list.length) {
      return {};
    }

    const instrument = list[0];
    const symName = String(instrument.symbol || '').trim();
    if (!symName) return {};

    // Carry the numeric token as a custom field so getBars and subscribeBars
    // can use it directly (faster lookup, avoids display-name ambiguity).
    const secId = String(
      instrument.instrument_token
      || instrument.security_id
      || instrument.token
      || ''
    ).trim();

    const display = String(
      instrument.display_symbol
      || instrument.display_name
      || instrument.symbol
    ).trim();
    const exch = String(instrument.exchange_segment || instrument.exchange || 'NSE')
      .replace(/_EQ$|_FNO$|_COMM$/, '').replace('BSE', 'BSE').replace('NSE', 'NSE');

    const resolved: UDFSymbol & { security_id?: string } = {
      name: symName,
      ticker: symName,
      full_name: display,
      exchange: exch,
      listed_exchange: exch,
      timezone: 'Asia/Kolkata',
      session: '0915-1530',
      session_regular: '0930-1600',
      minmov: 1,
      pricescale: Math.pow(10, instrument.price_precision || 2),
      type: 'stock',
      has_intraday: true,
      has_daily: true,
      has_weekly_and_monthly: true,
      // TradingView expects a string enum here (e.g. 'ohlcv' / 'c'), not an array.
      visible_plots_set: 'ohlcv',
      supported_resolutions: normalizeResolutions(instrument.supported_intervals),
      volume_precision: instrument.volume_precision || 0,
      data_status: 'streaming',
      description: display,
    };
    if (secId) (resolved as any).security_id = secId;
    return resolved;
  } catch (error) {
    console.error('Error resolving symbol:', error);
    return {};
  }
};

/**
 * UDF Search endpoint - search for symbols
 */
export const searchSymbols = async (
  query: string,
  _type: string,
  _exchange: string,
  maxRecords: number = 20
): Promise<Array<{ symbol: string; full_name: string; description: string; exchange: string; ticker: string; type: string }>> => {
  try {
    const response = await apiService.get(`/chart/instruments`, {
      query,
      limit: maxRecords,
    });

    const list = Array.isArray(response?.data)
      ? response.data
      : (Array.isArray(response) ? response : []);

    if (!list.length) {
      return [];
    }

    return list.map((instrument: any) => {
      const symName = String(instrument.symbol || '').trim();
      const display = String(
        instrument.display_symbol
        || instrument.display_name
        || instrument.symbol
        || symName
      ).trim();
      // Use the numeric token as ticker so resolveSymbol can bypass text search
      // for unambiguous lookup. The `symbol` field is what TradingView displays.
      const tickerVal = String(
        instrument.instrument_token
        || instrument.security_id
        || instrument.token
        || symName
      ).trim();
      const exch = String(instrument.exchange_segment || instrument.exchange || 'NSE')
        .replace(/_EQ$|_FNO$|_COMM$/, '');

      return {
        symbol: symName,
        full_name: display,
        description: display,
        exchange: exch,
        ticker: tickerVal,
        type: 'stock',
      };
    });
  } catch (error) {
    console.error('Error searching symbols:', error);
    return [];
  }
};

/**
 * UDF History endpoint - fetch bars/candles
 */
export const getBars = async (
  symbolInfo: UDFSymbol,
  resolution: string,
  from: number,
  to: number,
  _first: boolean = true,
  countback?: number
): Promise<{ bars: UDFBar[]; meta?: Record<string, any> }> => {
  try {
    const normalizedResolution = normalizeResolution(resolution);

    // Convert UDF resolution to Straddly interval format
    const intervalMap: Record<string, string> = {
      '1': '1m',
      '3': '3m',
      '5': '5m',
      '15': '15m',
      '25': '25m',
      '30': '30m',
      '60': '60m',
      'D': 'D',
      '1D': 'D',
    };

    const interval = intervalMap[normalizedResolution] || '5m';

    // Fetch history from backend — prefer instrument_id (numeric token) over symbol
    // name for faster, unambiguous resolution on the backend.
    const instId = (symbolInfo as any).security_id;
    const response = await apiService.get(`/chart/history`, {
      ...(instId ? { instrument_id: instId } : { symbol: symbolInfo.name }),
      interval,
      from: from * 1000, // Convert seconds to ms
      to: to * 1000,
      countback: countback,
    });

    if (!response?.ok) {
      // Throw so the wrapper calls onErrorCallback — avoids permanently marking as noData
      throw new Error(
        `chart/history API error: ${response?.data?.reason || response?.data?.message || String(response?.status ?? 'network')}`
      );
    }

    const candles = response.data?.candles || [];
    const bars: UDFBar[] = candles.map((candle: any) => ({
      time: Number(candle.timestamp || 0),
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
      volume: candle.volume,
    }));

    if (bars.length > 0) {
      const token = parseInt(String((symbolInfo as any).security_id || ''), 10);
      if (token && !isNaN(token)) {
        lastHistoryBars.set(_streamKey(token, normalizedResolution), bars[bars.length - 1]);
      }
    }

    return {
      bars,
      meta: { nodata: bars.length === 0 },
    };
  } catch (error) {
    console.error('Error fetching bars:', error);
    // Re-throw: the wrapper's .catch calls onErrorCallback which lets TradingView retry,
    // rather than returning noData=true which permanently silences the range.
    throw error;
  }
};

/**
 * UDF Quotes endpoint - get current quotes/prices
 */
export const getQuotes = async (
  symbols: string[]
): Promise<Record<string, any>> => {
  try {
    const response = await apiService.get(`/chart/quotes`, {
      symbols: symbols.join(','),
    });

    if (!response?.ok) {
      return {};
    }

    const quotes: Record<string, any> = {};
    (response.data || []).forEach((quote: any) => {
      quotes[quote.symbol] = {
        s: 'ok',
        n: quote.symbol,
        v: {
          ch: (quote.last_price || 0) - (quote.previous_close || 0),
          chp: ((quote.last_price || 0) - (quote.previous_close || 0)) / (quote.previous_close || 1) * 100,
          short_name: quote.symbol,
          exchange: quote.exchange,
          description: quote.symbol,
          lp: quote.last_price,
          ask: quote.last_price,
          bid: quote.last_price,
          type: 'stock',
          update_mode: 'streaming',
        },
      };
    });

    return quotes;
  } catch (error) {
    console.error('Error fetching quotes:', error);
    return {};
  }
};

// ── Real-time WebSocket manager for chart bar subscriptions (on-demand) ──
//
// Protocol for /api/v2/ws/feed:
//   Client sends: {"action": "subscribe",   "tokens": [numericId, ...]}
//   Client sends: {"action": "unsubscribe", "tokens": [numericId, ...]}
//   Server sends: {"type": "tick",     "data": {instrument_token, ltp, ...}}
//   Server sends: {"type": "snapshot", "data": [{instrument_token, ltp, ...}, ...]}

// Map subscriptionUID → callback
const realtimeCallbacks = new Map<string, (bar: UDFBar) => void>();
// Map subscriptionUID → numeric token (for unsubscribe)
const realtimeTokens = new Map<string, number>();
// Map numeric token → Set of subscriptionUIDs (many subs may share same token)
const tokenToSubs = new Map<number, Set<string>>();
// Map subscriptionUID → resolution string (e.g. '5', '15', 'D')
const realtimeResolutions = new Map<string, string>();
// UIDs currently awaiting WS connection — deleted by unsubscribeRealtime to cancel
const pendingSubscriptions = new Set<string>();
// Per-subscription current bar state for proper intraday OHLCV tracking
const currentBarState = new Map<string, UDFBar>();
// Last bar from getBars per token+resolution, used to seed realtime stream.
const lastHistoryBars = new Map<string, UDFBar>();

let globalWs: WebSocket | null = null;

const toEpochMs = (value: unknown): number | null => {
  if (value == null) return null;

  if (typeof value === 'number' && Number.isFinite(value)) {
    // Support both seconds and milliseconds.
    return value < 10_000_000_000 ? Math.trunc(value * 1000) : Math.trunc(value);
  }

  if (typeof value === 'string') {
    const raw = value.trim();
    if (!raw) return null;

    const asNum = Number(raw);
    if (Number.isFinite(asNum)) {
      return asNum < 10_000_000_000 ? Math.trunc(asNum * 1000) : Math.trunc(asNum);
    }

    const parsed = Date.parse(raw);
    if (Number.isFinite(parsed)) return parsed;
  }

  return null;
};

// Some vendor feeds occasionally deliver ltt shifted by +05:30 relative to
// expected epoch alignment. If a tick time is far in the future, normalize it
// back by IST offset so realtime bars align with currently visible candles.
const _normalizeRealtimeEpochMs = (ms: number): number => {
  const IST_OFFSET_MS = 330 * 60 * 1000;
  const now = Date.now();
  const FUTURE_THRESHOLD_MS = 2 * 60 * 60 * 1000;
  if (ms - now > FUTURE_THRESHOLD_MS) {
    const shifted = ms - IST_OFFSET_MS;
    // Apply only when shifted value looks plausible around current wall clock.
    if (Math.abs(shifted - now) < 12 * 60 * 60 * 1000) {
      return shifted;
    }
  }
  return ms;
};

// Floor a millisecond timestamp to the open time of the candle it belongs to.
// TradingView requires bar.time == candle open time, not the raw tick time.
const _floorToCandle = (ms: number, resolution: string): number => {
  if (resolution === 'D' || resolution === '1D') {
    // Floor to midnight IST (UTC+5:30 = 330 min offset)
    const IST_OFFSET_MS = 330 * 60 * 1000;
    const dayMs = 24 * 60 * 60 * 1000;
    return Math.floor((ms + IST_OFFSET_MS) / dayMs) * dayMs - IST_OFFSET_MS;
  }
  const minutes = parseInt(resolution, 10);
  if (!minutes || isNaN(minutes)) return ms;
  const periodMs = minutes * 60 * 1000;
  return Math.floor(ms / periodMs) * periodMs;
};

const _streamKey = (token: number, resolution: string): string => {
  return `${token}|${normalizeResolution(resolution)}`;
};

const _dispatchTick = (tickData: any): void => {
  const tokenNum = Number(tickData?.instrument_token ?? tickData?.token);
  if (!tokenNum) return;
  const subsForToken = tokenToSubs.get(tokenNum);
  if (!subsForToken || subsForToken.size === 0) return;

  const rawTimeMs =
    toEpochMs(tickData?.ltt)
    ?? toEpochMs(tickData?.timestamp)
    ?? toEpochMs(tickData?.updated_at)
    ?? Date.now();
  const effectiveTimeMs = _normalizeRealtimeEpochMs(rawTimeMs);

  subsForToken.forEach((uid) => {
    const cb = realtimeCallbacks.get(uid);
    if (!cb) return;
    const resolution = realtimeResolutions.get(uid) || '5';
    const candleTime = _floorToCandle(effectiveTimeMs, resolution);
    const ltp = parseFloat(tickData.ltp ?? 0);
    if (!ltp) return;

    // Maintain per-subscription candle state so OHLCV tracks the current candle,
    // not the day's cumulative OHLCV which is wrong for intraday bars.
    const prev = currentBarState.get(uid);
    let bar: UDFBar;
    if (!prev || prev.time !== candleTime) {
      // First tick of a new candle — open = ltp
      bar = { time: candleTime, open: ltp, high: ltp, low: ltp, close: ltp, volume: parseInt(tickData.volume ?? 0, 10) };
    } else {
      bar = {
        time: candleTime,
        open: prev.open,
        high: Math.max(prev.high, ltp),
        low: Math.min(prev.low, ltp),
        close: ltp,
        volume: parseInt(tickData.volume ?? 0, 10),
      };
    }
    currentBarState.set(uid, bar);

    try {
      cb(bar);
    } catch (e) {
      console.error('[Chart RT] onRealtimeCallback error uid=', uid, e);
    }
  });
};

const ensureRealtimeConnection = (): Promise<WebSocket> => {
  return new Promise((resolve, reject) => {
    if (globalWs && globalWs.readyState === WebSocket.OPEN) {
      resolve(globalWs);
      return;
    }

    // Already connecting — piggyback instead of creating a duplicate connection
    if (globalWs && globalWs.readyState === WebSocket.CONNECTING) {
      const ws = globalWs;
      ws.addEventListener('open', () => resolve(ws), { once: true });
      ws.addEventListener('error', (err) => reject(err), { once: true });
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/v2/ws/feed`;

    globalWs = new WebSocket(wsUrl);
    globalWs.onopen = () => {
      console.log('[Chart RT] WebSocket connected:', wsUrl);
      resolve(globalWs!);
    };
    globalWs.onerror = (err) => {
      console.error('[Chart RT] WebSocket error:', err);
      reject(err);
    };
    globalWs.onclose = () => {
      console.log('[Chart RT] WebSocket closed');
      globalWs = null;
    };
    globalWs.onmessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'tick') {
          _dispatchTick(msg.data);
        } else if (msg.type === 'snapshot' && Array.isArray(msg.data)) {
          msg.data.forEach(_dispatchTick);
        }
      } catch (e) {
        console.warn('[Chart RT] Failed to parse message:', e);
      }
    };
  });
};

const subscribeRealtime = async (
  symbolInfo: UDFSymbol,
  onRealtimeCallback: (bar: UDFBar) => void,
  subscriptionUID: string,
  resolution: string = '5'
) => {
  const numericToken = parseInt(String((symbolInfo as any).security_id || ''), 10);
  if (!numericToken || isNaN(numericToken)) {
    console.warn('[Chart RT] No numeric token available for', symbolInfo.name, '— skip WS subscribe');
    return;
  }

  // Register as pending BEFORE the await so unsubscribeBars can cancel us if TV
  // switches symbols during WS setup (a common race with saved-state restoration).
  pendingSubscriptions.add(subscriptionUID);

  try {
    const ws = await ensureRealtimeConnection();

    // If unsubscribeBars fired while we were awaiting, honour the cancellation.
    if (!pendingSubscriptions.has(subscriptionUID)) {
      console.log(`[Chart RT] Subscription cancelled during WS setup uid=${subscriptionUID}`);
      return;
    }
    pendingSubscriptions.delete(subscriptionUID);

    // Set up state AFTER the await so a concurrent unsubscribeBars finds nothing to remove.
    realtimeCallbacks.set(subscriptionUID, onRealtimeCallback);
    realtimeTokens.set(subscriptionUID, numericToken);
    realtimeResolutions.set(subscriptionUID, resolution);

    const seed = lastHistoryBars.get(_streamKey(numericToken, resolution));
    if (seed) {
      currentBarState.set(subscriptionUID, { ...seed });
      console.log('[Chart RT] Seeded realtime from history token=', numericToken, 'resolution=', normalizeResolution(resolution), 'time=', seed.time);
    }

    if (!tokenToSubs.has(numericToken)) tokenToSubs.set(numericToken, new Set());
    tokenToSubs.get(numericToken)!.add(subscriptionUID);

    ws.send(JSON.stringify({ action: 'subscribe', tokens: [numericToken] }));
    console.log(`[Chart RT] Subscribed token=${numericToken} uid=${subscriptionUID} resolution=${resolution}`);
  } catch (err) {
    pendingSubscriptions.delete(subscriptionUID);
    console.error('[Chart RT] Subscription failed:', err);
  }
};

const unsubscribeRealtime = (subscriptionUID: string) => {
  // Cancel any in-flight subscription waiting on WS connection.
  pendingSubscriptions.delete(subscriptionUID);
  currentBarState.delete(subscriptionUID);

  const numericToken = realtimeTokens.get(subscriptionUID);
  realtimeCallbacks.delete(subscriptionUID);
  realtimeTokens.delete(subscriptionUID);
  realtimeResolutions.delete(subscriptionUID);

  if (numericToken !== undefined) {
    const subs = tokenToSubs.get(numericToken);
    if (subs) {
      subs.delete(subscriptionUID);
      if (subs.size === 0) {
        tokenToSubs.delete(numericToken);
        // Unsubscribe on server only when no other chart needs this token.
        if (globalWs?.readyState === WebSocket.OPEN) {
          globalWs.send(JSON.stringify({ action: 'unsubscribe', tokens: [numericToken] }));
        }
      }
    }
  }

  console.log(`[Chart RT] Unsubscribed uid=${subscriptionUID}`);
  // Keep WS alive — symbol switches briefly hit 0 callbacks and closing here
  // causes the next subscribe to fail with "closed before connection established"
};

/**
 * Create UDF-compatible datafeed object for TradingView Widget
 */
export const createUdfDatafeed = () => {
  return {
    onReady: (callback: (config: UDFConfig) => void) => {
      setTimeout(() => {
        callback(getUdfConfig());
      }, 0);
    },

    searchSymbols: (
      userInput: string,
      exchange: string,
      symbolType: string,
      onResultReadyCallback: (results: Array<{ symbol: string; full_name: string; description: string; exchange: string; ticker: string; type: string }>) => void
    ) => {
      searchSymbols(userInput, symbolType, exchange)
        .then((results) => onResultReadyCallback(results))
        .catch(() => onResultReadyCallback([]));
    },

    resolveSymbol: (symbolName: string, onSymbolResolvedCallback: (symbol: UDFSymbol) => void, onResolveErrorCallback: (error: string) => void) => {
      resolveSymbol(symbolName)
        .then((symbol) => {
          if (symbol && symbol.name) {
            onSymbolResolvedCallback(symbol as UDFSymbol);
          } else {
            onResolveErrorCallback('Cannot resolve symbol');
          }
        })
        .catch((error) => onResolveErrorCallback(String(error)));
    },

    getBars: (
      symbolInfo: UDFSymbol,
      resolution: string,
      periodParamsOrFrom: any,
      onHistoryCallbackOrTo: any,
      onErrorCallbackOrHistory: any,
      maybeOnErrorOrFirst?: any,
      maybeFirst?: any
    ) => {
      // Support both TradingView signatures:
      // 1) getBars(symbolInfo, resolution, periodParams, onHistory, onError)
      // 2) getBars(symbolInfo, resolution, from, to, onHistory, onError, firstDataRequest)
      let from: number | undefined;
      let to: number | undefined;
      let countBack: number | undefined;
      let firstDataRequest = true;
      let onHistoryCallback: (bars: UDFBar[], metadata: { noData?: boolean; nodata?: boolean }) => void;
      let onErrorCallback: (error: string) => void;

      if (periodParamsOrFrom && typeof periodParamsOrFrom === 'object') {
        from = Number(periodParamsOrFrom.from);
        to = Number(periodParamsOrFrom.to);
        countBack = Number(periodParamsOrFrom.countBack ?? periodParamsOrFrom.countback);
        firstDataRequest = Boolean(periodParamsOrFrom.firstDataRequest);
        onHistoryCallback = onHistoryCallbackOrTo;
        onErrorCallback = onErrorCallbackOrHistory;
      } else {
        from = Number(periodParamsOrFrom);
        to = Number(onHistoryCallbackOrTo);
        onHistoryCallback = onErrorCallbackOrHistory;
        onErrorCallback = maybeOnErrorOrFirst;
        firstDataRequest = Boolean(maybeFirst);
      }

      const nowSec = Math.floor(Date.now() / 1000);
      const safeTo = Number.isFinite(to) && (to as number) > 0 ? (to as number) : nowSec;
      const safeFrom = Number.isFinite(from) && (from as number) > 0 ? (from as number) : (safeTo - (30 * 24 * 60 * 60));
      const safeCountBack = Number.isFinite(countBack) && (countBack as number) > 0 ? Number(countBack) : undefined;

      // On firstDataRequest, cap how far back we ask for intraday data.
      // This prevents the daily viewport's date range from flooding the backend with
      // enormous head-gap-fill loops when the user switches from a daily to intraday TF.
      const MAX_INTRADAY_LOOKBACK_SEC = 90 * 24 * 60 * 60; // 90 days
      const normalizedResolution = normalizeResolution(resolution);
      const isIntraday = normalizedResolution !== 'D' && normalizedResolution !== 'W' && normalizedResolution !== 'M';
      const effectiveFrom = (firstDataRequest && isIntraday)
        ? Math.max(safeFrom, safeTo - MAX_INTRADAY_LOOKBACK_SEC)
        : safeFrom;

      getBars(symbolInfo, resolution, effectiveFrom, safeTo, firstDataRequest, safeCountBack)
        .then(({ bars, meta }) => {
          onHistoryCallback(bars, {
            noData: Boolean(meta?.nodata),
            nodata: Boolean(meta?.nodata),
          });
        })
        .catch((error) => onErrorCallback(String(error)));
    },

    subscribeBars: (
      symbolInfo: UDFSymbol,
      resolution: string,
      onRealtimeCallback: (bar: UDFBar) => void,
      subscriptionUID: string,
      _onResetCacheNeededCallback: () => void
    ) => {
      // On-demand subscription to real-time updates via WebSocket
      subscribeRealtime(symbolInfo, onRealtimeCallback, subscriptionUID, resolution).catch((err) => {
        console.error(`[Chart RT] Failed to subscribe for ${symbolInfo.name}:`, err);
      });
    },

    unsubscribeBars: (subscriptionUID: string) => {
      unsubscribeRealtime(subscriptionUID);
    },

    getServerTime: async (callback: (time: number) => void) => {
      try {
        const response = await apiService.get('/auth/me');
        if (response?.timestamp) {
          callback(Math.floor(response.timestamp / 1000)); // seconds
          return;
        }
      } catch {
        // Fallback to client time
      }
      callback(Math.floor(Date.now() / 1000));
    },
  };
};
