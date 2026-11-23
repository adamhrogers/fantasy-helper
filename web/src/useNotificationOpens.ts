import { useEffect } from 'react';

/** Tapping a notification while the app is open: the service worker focuses the window and posts
 *  {type: 'OPEN', path}; this passes the path on (the app switches to that tab and refreshes). */
export function useNotificationOpens(onOpen: (path: string) => void): void {
  useEffect(() => {
    const container = typeof navigator === 'undefined' ? undefined : navigator.serviceWorker;
    if (!container) return; // (not every browser, and not the test environment, has one)
    const listener = (event: MessageEvent) => {
      const data = event.data as { type?: unknown; path?: unknown } | null;
      if (data?.type === 'OPEN' && typeof data.path === 'string') onOpen(data.path);
    };
    container.addEventListener('message', listener);
    return () => container.removeEventListener('message', listener);
  }, [onOpen]);
}
