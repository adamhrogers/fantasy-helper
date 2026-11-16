import react from '@vitejs/plugin-react';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { VitePWA } from 'vite-plugin-pwa';
import { defineConfig } from 'vitest/config';
import { loadEnv, type Plugin } from 'vite';
import { buildId } from './build-id';
import { renderHeaders } from './headers';

const root = fileURLToPath(new URL('.', import.meta.url));

/** Writes dist/_headers from headers.template with this build's own API and Supabase addresses. */
function headersFile(env: Record<string, string>): Plugin {
  return {
    name: 'headers-file',
    generateBundle() {
      const template = readFileSync(new URL('./headers.template', import.meta.url), 'utf8');
      this.emitFile({ type: 'asset', fileName: '_headers', source: renderHeaders(template, env) });
    },
  };
}

export default defineConfig(({ mode }) => {
  // The build variables: the ones in Cloudflare's build environment, or a local .env file.
  const env = loadEnv(mode, root, 'VITE_');
  return {
    // Shown in the app's footer; a hash of the app's sources and build variables (build-id.ts).
    define: { __APP_BUILD__: JSON.stringify(buildId(root, env)) },
    plugins: [
      react(),
      headersFile(env),
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
      include: ['src/**/*.test.{ts,tsx}', 'build-id.test.ts', 'headers.test.ts'],
    },
  };
});
