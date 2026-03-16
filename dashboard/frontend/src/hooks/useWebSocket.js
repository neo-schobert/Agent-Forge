import { useState, useEffect, useRef, useCallback } from 'react';

export function useWebSocket(url, options = {}) {
  const [lastMessage, setLastMessage] = useState(null);
  const [readyState, setReadyState] = useState('connecting');
  const wsRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const reconnectAttemptsRef = useRef(0);
  const maxReconnectAttempts = options.maxReconnectAttempts ?? 10;
  const shouldReconnect = options.reconnect !== false;
  const mountedRef = useRef(true);

  const connect = useCallback(() => {
    if (!url) return;
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;

    try {
      const wsUrl = url.startsWith('ws') ? url : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}${url}`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        setReadyState('open');
        reconnectAttemptsRef.current = 0;
        if (options.onOpen) options.onOpen();
      };

      ws.onmessage = (event) => {
        if (!mountedRef.current) return;
        let parsed;
        try {
          parsed = JSON.parse(event.data);
        } catch {
          parsed = event.data;
        }
        setLastMessage({ data: parsed, raw: event.data, timestamp: Date.now() });
        if (options.onMessage) options.onMessage(parsed, event);
      };

      ws.onerror = (error) => {
        if (!mountedRef.current) return;
        if (options.onError) options.onError(error);
      };

      ws.onclose = (event) => {
        if (!mountedRef.current) return;
        setReadyState('closed');
        if (options.onClose) options.onClose(event);

        if (shouldReconnect && reconnectAttemptsRef.current < maxReconnectAttempts) {
          const delay = Math.min(1000 * Math.pow(2, reconnectAttemptsRef.current), 30000);
          reconnectAttemptsRef.current += 1;
          reconnectTimeoutRef.current = setTimeout(() => {
            if (mountedRef.current) {
              setReadyState('connecting');
              connect();
            }
          }, delay);
        }
      };
    } catch (err) {
      setReadyState('closed');
      if (options.onError) options.onError(err);
    }
  }, [url]);

  const reconnect = useCallback(() => {
    reconnectAttemptsRef.current = 0;
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
    }
    clearTimeout(reconnectTimeoutRef.current);
    setReadyState('connecting');
    connect();
  }, [connect]);

  const sendMessage = useCallback((data) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      const msg = typeof data === 'string' ? data : JSON.stringify(data);
      wsRef.current.send(msg);
      return true;
    }
    return false;
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      clearTimeout(reconnectTimeoutRef.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
    };
  }, [connect]);

  return { lastMessage, sendMessage, readyState, reconnect };
}
