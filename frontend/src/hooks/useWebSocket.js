import { useState, useEffect, useRef, useCallback } from 'react';

const MAX_RECONNECTS = 3;
const BASE_DELAY_MS  = 1000;

/**
 * useWebSocket — manages a WebSocket connection with auto-reconnect.
 * @param {string|null} url — if null/undefined the socket is not opened.
 */
export function useWebSocket(url) {
  const [lastMessage, setLastMessage] = useState(null);
  const [readyState, setReadyState]   = useState(WebSocket.CLOSED);

  const wsRef          = useRef(null);
  const reconnectCount = useRef(0);
  const timerRef       = useRef(null);
  const mountedRef     = useRef(true);

  const connect = useCallback(() => {
    if (!url || !mountedRef.current) return;

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        reconnectCount.current = 0;
        setReadyState(WebSocket.OPEN);
      };

      ws.onmessage = (evt) => {
        if (!mountedRef.current) return;
        try {
          setLastMessage(JSON.parse(evt.data));
        } catch {
          setLastMessage(evt.data);
        }
      };

      ws.onerror = () => { /* handled by onclose */ };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        setReadyState(WebSocket.CLOSED);
        if (reconnectCount.current < MAX_RECONNECTS) {
          const delay = BASE_DELAY_MS * Math.pow(2, reconnectCount.current);
          reconnectCount.current += 1;
          timerRef.current = setTimeout(connect, delay);
        }
      };

      setReadyState(WebSocket.CONNECTING);
    } catch (e) {
      console.warn('[useWebSocket] failed to open:', e);
    }
  }, [url]);

  const reconnect = useCallback(() => {
    reconnectCount.current = 0;
    if (wsRef.current) wsRef.current.close();
    connect();
  }, [connect]);

  const sendMessage = useCallback((data) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(typeof data === 'string' ? data : JSON.stringify(data));
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      clearTimeout(timerRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [connect]);

  return { lastMessage, sendMessage, readyState, reconnect };
}

export default useWebSocket;
