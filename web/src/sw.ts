/// <reference lib="webworker" />
// The service worker: it keeps the app's own files (the shell) so the app opens instantly, and
// nothing else. Requests to the API match no route here, so they always go to the network and
// nothing signed-in is ever cached. Phase 4 adds push to this file.
import { clientsClaim } from 'workbox-core';
import { cleanupOutdatedCaches, createHandlerBoundToURL, precacheAndRoute } from 'workbox-precaching';
import type { PrecacheEntry } from 'workbox-precaching';
import { NavigationRoute, registerRoute } from 'workbox-routing';

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

clientsClaim();
