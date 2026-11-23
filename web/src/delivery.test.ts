// Checks on the files Cloudflare serves and the service worker, which only run in a browser on
// the deployed address: these pin the properties that matter, so a change can't lose them quietly.
import { describe, expect, it } from 'vitest';
import headers from '../headers.template?raw';
import config from '../vite.config.ts?raw';
import worker from './sw.ts?raw';

describe('the service worker', () => {
  it('keeps the app shell and nothing else: no runtime caching, so no API response is ever stored', () => {
    expect(worker).toContain('precacheAndRoute(self.__WB_MANIFEST)');
    for (const strategy of ['CacheFirst', 'NetworkFirst', 'StaleWhileRevalidate', 'registerRoute(({', 'caches.open']) {
      expect(worker).not.toContain(strategy);
    }
    // The one extra route is the navigation fallback to the cached shell.
    expect(worker.match(/registerRoute\(/g)).toHaveLength(1);
    expect(worker).toContain('NavigationRoute');
  });

  it('shows a notification for every push, even one it can’t read: Safari on iOS drops apps that show none', () => {
    const handler = /addEventListener\('push'[\s\S]*?\n\}\);/.exec(worker)?.[0] ?? '';
    expect(handler).toContain('showNotification');
    expect(handler).toContain('describeNotification'); // which always gives something to show
    expect(handler).not.toMatch(/\breturn\b/); // nothing can leave the handler before it shows one
    expect(handler).not.toMatch(/\bif \(/); // and nothing decides not to
    expect(worker).not.toContain('userVisibleOnly: false');
  });

  it('opens the app on a tap, only on its own two tabs', () => {
    const handler = /addEventListener\('notificationclick'[\s\S]*?\n\}\);/.exec(worker)?.[0] ?? '';
    expect(handler).toContain('event.notification.close()');
    expect(handler).toContain('safePath('); // a path from the message is never opened as it came
    expect(worker).toContain('clients.openWindow(path)');
    expect(worker).not.toMatch(/openWindow\((?!path\))/);
  });

  it('is registered by the page, which decides when an update takes over', () => {
    expect(config).toContain("registerType: 'prompt'");
    expect(config).toContain("strategies: 'injectManifest'");
    expect(worker).toContain("'SKIP_WAITING'");
  });
});

describe('the headers Cloudflare sends', () => {
  it('lets only this app’s own files run, and only the two addresses the build supplies be called', () => {
    const csp = /Content-Security-Policy: (.*)/.exec(headers)?.[1] ?? '';
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("script-src 'self'");
    expect(csp).not.toContain('unsafe-inline');
    expect(csp).not.toContain('unsafe-eval');
    expect(csp).toContain("connect-src 'self' {{CONNECT_ORIGINS}};");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
  });

  it('names no project, address or wildcard host: the build fills the two addresses in', () => {
    expect(headers.match(/https?:\/\/\S+/g) ?? []).toEqual([]);
    expect(headers).not.toContain('*.run.app');
    expect(headers).not.toContain('*.supabase.co');
  });

  it('checks the service worker on every open, and keeps hashed files for a year', () => {
    expect(headers).toMatch(/\/sw\.js\s+Cache-Control: no-cache/);
    expect(headers).toMatch(/\/assets\/\*\s+Cache-Control: public, max-age=31536000, immutable/);
  });

  it('turns off browser features the app never uses, and sends no referrer', () => {
    expect(headers).toContain('Referrer-Policy: no-referrer');
    expect(headers).toContain('X-Content-Type-Options: nosniff');
    expect(headers).toContain('Permissions-Policy: camera=(), microphone=(), geolocation=()');
  });
});
