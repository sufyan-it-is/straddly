import { useState, useEffect, useRef } from 'react';
import { apiService } from '../services/apiService';
import { useWebSocket } from './useWebSocket';

/**
 * useMarketPulse — subscribes to the backend /api/v2/ws/prices WebSocket.
 * Broadcasts: { timestamp, status, prices: { NIFTY: 24000, BANKNIFTY: 52000, ... } }
 */
export function useMarketPulse() {
  const [pulse, setPulse] = useState({
    timestamp:    null,
    status:       'unknown',
    prices:       {},
    marketActive: false,
    marketActiveEquity: false,
    marketActiveCommodity: false,
  });

  // Build WS URL from apiService.baseURL — handles dev proxy and production
  const wsUrl = (() => {
    try {
      const base = apiService.baseURL; // e.g. '/api/v2'
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.host;
      // If base is a relative path, prepend host
      if (base.startsWith('/')) {
        return `${protocol}//${host}${base}/ws/prices`;
      }
      // If base is absolute (like in production)
      return base.replace(/^https?:/, protocol === 'wss:' ? 'wss:' : 'ws:') + '/ws/prices';
    } catch {
      return null;
    }
  })();

  const { lastMessage, readyState } = useWebSocket(wsUrl);

  useEffect(() => {
    if (!lastMessage) return;
    const data = typeof lastMessage === 'string' ? JSON.parse(lastMessage) : lastMessage;

    // Backend may send either:
    //  1) { timestamp, status, market_active, prices: {...} }
    //  2) { type: 'prices', data: { prices: {...} } }
    const prices = data?.prices || data?.data?.prices || data?.ltp || {};
    const ts = data?.timestamp || new Date().toISOString();

    const marketActive = data?.market_active !== false;
    const marketActiveEquity = data?.market_active_equity !== false;
    const marketActiveCommodity = data?.market_active_commodity !== false;

    setPulse({
      timestamp: ts,
      status: data?.status || 'active',
      prices,
      marketActive,
      marketActiveEquity,
      marketActiveCommodity,
    });
  }, [lastMessage]);

  return { pulse, readyState, marketActive: pulse.marketActive };
}

export default useMarketPulse;
