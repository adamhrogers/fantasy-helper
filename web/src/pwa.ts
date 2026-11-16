import { useSyncExternalStore } from 'react';
import { registerSW } from 'virtual:pwa-register';

// The page registers the service worker and decides when a new version takes over: an update
// shows a bar ("Update") instead of reloading the page under someone who is typing.

let apply: (() => void) | null = null;
const listeners = new Set<() => void>();

function announce(next: (() => void) | null): void {
  apply = next;
  listeners.forEach((listener) => listener());
}

export function startServiceWorker(): void {
  // Service workers only run on HTTPS (and localhost); the dev server has none (vite.config.ts).
  if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return;
  const update = registerSW({
    onNeedRefresh() {
      announce(() => void update(true));
    },
    onRegisteredSW(_url, registration) {
      if (!registration) return;
      // A phone app is suspended, not closed, so the browser's own check (on a page load) may not
      // come for days: look for a new version whenever the app returns, and hourly while it's open.
      const check = () => void registration.update().catch(() => undefined);
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') check();
      });
      window.setInterval(check, 60 * 60_000);
    },
  });
}

/** The function that applies a waiting update, or null when there is none. */
export function usePendingUpdate(): (() => void) | null {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => apply,
  );
}
