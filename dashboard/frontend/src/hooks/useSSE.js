import { useState, useEffect, useRef, useCallback } from 'react';

export function useSSE(url, options = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [isConnected, setIsConnected] = useState(false);
  const esRef = useRef(null);
  const mountedRef = useRef(true);

  const close = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    if (mountedRef.current) {
      setIsConnected(false);
    }
  }, []);

  useEffect(() => {
    if (!url) return;
    mountedRef.current = true;

    const connect = () => {
      try {
        const es = new EventSource(url);
        esRef.current = es;

        es.onopen = () => {
          if (!mountedRef.current) return;
          setIsConnected(true);
          setError(null);
          if (options.onOpen) options.onOpen();
        };

        es.onmessage = (event) => {
          if (!mountedRef.current) return;
          let parsed;
          try {
            parsed = JSON.parse(event.data);
          } catch {
            parsed = event.data;
          }
          setData(parsed);
          if (options.onMessage) options.onMessage(parsed, event);
        };

        es.onerror = (err) => {
          if (!mountedRef.current) return;
          setIsConnected(false);
          setError(err);
          if (options.onError) options.onError(err);
          es.close();
          esRef.current = null;
        };

        // Handle named events if specified
        if (options.events && Array.isArray(options.events)) {
          options.events.forEach((eventName) => {
            es.addEventListener(eventName, (event) => {
              if (!mountedRef.current) return;
              let parsed;
              try {
                parsed = JSON.parse(event.data);
              } catch {
                parsed = event.data;
              }
              if (options.onEvent) options.onEvent(eventName, parsed, event);
            });
          });
        }
      } catch (err) {
        if (mountedRef.current) {
          setError(err);
          setIsConnected(false);
        }
      }
    };

    connect();

    return () => {
      mountedRef.current = false;
      close();
    };
  }, [url]);

  return { data, error, isConnected, close };
}
