/// <reference lib="webworker" />
// The service worker: it keeps the app's own files (the shell) so the app opens instantly, shows a
// notification when the server pushes one, and does nothing else. Requests to the API match no
// route here, so they always go to the network and nothing signed-in is ever cached.
import { clientsClaim } from 'workbox-core';
import { cleanupOutdatedCaches, createHandlerBoundToURL, precacheAndRoute } from 'workbox-precaching';
import type { PrecacheEntry } from 'workbox-precaching';
import { NavigationRoute, registerRoute } from 'workbox-routing';
import { describeNotification, safePath, type NotificationPath } from './notification';

declare const self: ServiceWorkerGlobalScope & { __WB_MANIFEST: Array<PrecacheEntry | string> };

// The page asks for the update when the person taps "Update" (src/pwa.ts).
self.addEventListener('message', (event) => {
  if ((event.data as { type?: string } | null)?.type === 'SKIP_WAITING') void self.skipWaiting();
});

cleanupOutdatedCaches();
precacheAndRoute(self.__WB_MANIFEST);

// Opening /today or /week (or anything that isn't a file) serves the cached shell.
registerRoute(
  new NavigationRoute(createHandlerBoundToURL('/index.html'), {
    denylist: [/\/[^/?]+\.[^/]+$/], // real files (manifest, icons, sw.js) are never the shell
  }),
);

// A push always shows a notification, even when its text can't be read (describeNotification gives
// a plain one): Safari on iOS stops delivering to an app that receives a push and shows nothing.
self.addEventListener('push', (event) => {
  const shown = describeNotification(textOf(event));
  event.waitUntil(
    self.registration.showNotification(shown.title, {
      body: shown.body,
      icon: '/icon-192.png',
      data: { path: shown.path },
      ...(shown.tag ? { tag: shown.tag } : {}),
    }),
  );
});

function textOf(event: PushEvent): string | null {
  try {
    return event.data?.text() ?? null;
  } catch {
    return null; // a message that can't be read still shows the plain notification
  }
}

// A tap opens the app on the tab the message named: the window that is already open if there is
// one (it is told, and refreshes), else a new one.
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const path = safePath((event.notification.data as { path?: unknown } | null)?.path);
  event.waitUntil(open(path));
});

async function open(path: NotificationPath): Promise<void> {
  const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  const existing = windows[0];
  if (existing) {
    existing.postMessage({ type: 'OPEN', path });
    await existing.focus();
    return;
  }
  await self.clients.openWindow(path);
}

clientsClaim();
