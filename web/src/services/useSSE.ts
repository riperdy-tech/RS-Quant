import { useEffect, useRef, useState } from 'react';
import { SSEEventEnvelope } from '../types/api';

export function useSSE(onEvent?: (event: SSEEventEnvelope) => void) {
  const [isConnected, setIsConnected] = useState(false);
  const [isStale, setIsStale] = useState(false);
  const [lastEvent, setLastEvent] = useState<SSEEventEnvelope | null>(null);
  const lastMsgTimeRef = useRef<number>(Date.now());
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    let unmounted = false;
    const connect = () => {
      if (unmounted) return;
      const es = new EventSource('/api/v1/events');
      eventSourceRef.current = es;

      es.onopen = () => {
        setIsConnected(true);
        setIsStale(false);
        lastMsgTimeRef.current = Date.now();
      };

      es.onerror = () => {
        setIsConnected(false);
        setIsStale(true);
      };

      const handleMessage = (evt: MessageEvent) => {
        lastMsgTimeRef.current = Date.now();
        setIsStale(false);
        try {
          const parsed = JSON.parse(evt.data);
          if (parsed && typeof parsed === 'object') {
            setLastEvent(parsed);
            if (onEvent) onEvent(parsed);
          }
        } catch {
          // ignore non-JSON pings
        }
      };

      es.onmessage = handleMessage;
      es.addEventListener('resync', () => {
        lastMsgTimeRef.current = Date.now();
        setIsStale(false);
      });
      es.addEventListener('connected', () => {
        lastMsgTimeRef.current = Date.now();
        setIsStale(false);
      });
    };

    connect();

    // 3-second freshness timer per §15.1 and §17
    const timer = setInterval(() => {
      if (Date.now() - lastMsgTimeRef.current > 3500) {
        setIsStale(true);
      } else {
        setIsStale(false);
      }
    }, 1000);

    return () => {
      unmounted = true;
      clearInterval(timer);
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  return { isConnected, isStale, lastEvent };
}
