import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { VitePWA } from 'vite-plugin-pwa';
import { defineConfig } from 'vitest/config';
import { buildId } from './build-id';

export default defineConfig({
  // Shown in the app's footer; a hash of the app's sources (build-id.ts says why).
  define: { __APP_BUILD__: JSON.stringify(buildId(fileURLToPath(new URL('.', import.meta.url)))) },
  plugins: [
    react(),
    VitePWA({
      // A hand-written service worker (src/sw.ts): Phase 4 adds push to it.
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.ts',
      // The page decides when to update (src/pwa.ts), and registers the worker itself.
      registerType: 'prompt',
      injectRegister: false,
      injectManifest: {
        // The app shell and nothing else: the page, its scripts and styles, the favicon.
        // Nothing the API returns is ever cached (src/sw.ts).
        globPatterns: ['index.html', 'assets/**/*.{js,css}', 'favicon.svg'],
      },
      manifest: {
        id: '/',
        name: 'Fantasy Helper',
        short_name: 'Fantasy',
        description: 'Your daily fantasy basketball report and weekly plan.',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        orientation: 'portrait',
        theme_color: '#14181f',
        background_color: '#14181f',
        icons: [
          { src: '/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
          { src: '/icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      devOptions: { enabled: false },
    }),
  ],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}', 'build-id.test.ts'],
  },
});
