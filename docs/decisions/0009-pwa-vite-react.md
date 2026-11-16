# ADR 0009: Phone app as a PWA built with Vite, React and TypeScript

**Status:** Accepted · 2026-10-19

## Decision
An installable PWA (Vite, React, TypeScript, vite-plugin-pwa) with Web Push, hosted on Cloudflare (Workers static assets; see the Update below). There is no native app until the beta proves demand (spec).

## Why
It installs to the home screen, sends push on iPhone and Android, and updates instantly while the product changes every week. React has the largest ecosystem for swipeable cards and charts (Recharts for the timeline graphs).

## Rejected
- **SvelteKit:** lighter, but a smaller ecosystem.
- **Native app:** App Store friction before the product is proven.

## Consequences
iOS push requires adding the app to the Home Screen (iOS 16.4+), so beta onboarding includes that step.

## Update 2026-11-16: Workers, not Pages
The app is hosted on **Cloudflare Workers with static assets**, not Cloudflare Pages (Adam's decision). Cloudflare's own docs now say to start new projects on Workers, which serves a static single-page app the same way and for free (`not_found_handling: "single-page-application"`), and is where their new work goes. Pages still works and is the fallback if anything about Workers gets in the way. A custom domain on Workers needs the domain's DNS on Cloudflare (see [m3-plan.md](../m3-plan.md), Phase 3).

## Update 2026-11-16: how the app was built (M3 Phase 3)
- **Static assets honor `_headers`.** Checked by running Cloudflare's own `wrangler dev` against the built app: the security headers arrive on `/`, on the single-page-app fallback, and on assets and icons; `sw.js` goes out `Cache-Control: no-cache` and `/assets/*` immutable. The content-security policy allows the app to call exactly two addresses, its API and its Supabase project, and no inline script or style. Because this repo is public and those addresses are Adam's, they aren't in it: `web/headers.template` holds placeholders and the build (`vite.config.ts`, `headers.ts`) writes `dist/_headers` from the same build variables the app reads. (The first version allowed any `*.run.app` and `*.supabase.co`, which would have let an injected script send the sign-in token to someone else's service there; found in review, fixed 2026-11-16.) With a variable missing the policy allows no outside address, and the app shows its "build isn't set up" screen anyway. The app also refuses to start with a Supabase secret key (`sb_secret_...`, or a `service_role` token) in the publishable-key variable, since anything in the bundle is public.
- **`web/wrangler.jsonc`** has no `main`, only `assets.directory: "./dist"` and `not_found_handling: "single-page-application"`: an assets-only Worker. The Worker's name (`fantasy-helper`) must match the dashboard's, and the build reads it from the file.
- **Node and npm are pinned.** `web/.node-version` pins Node 24 for CI and Cloudflare (jsdom 31, which the tests need, doesn't run on Node 23). `web/package.json`'s `packageManager` pins npm 11: Cloudflare's default npm 10 crashes installing the test runner (an arborist bug), and Workers Builds honors the field.
- **Sign-in is email and password** (`signInWithPassword`), for Adam alone: it needs no email service. The emailed 6-digit code (`signInWithOtp` with `shouldCreateUser: false`, then `verifyOtp`) is built and tested but behind the build variable `VITE_SIGN_IN_WITH_CODE=true`, off by default, because Supabase's built-in email sender can't reach friends (the beta's SMTP and domain come first). The app never creates users; sign-ups stay off in Supabase. The session lives in the browser's own storage, so the installed Home Screen app signs in separately from Safari, and the app says so.
- **The service worker is hand-written** (`injectManifest`, `registerType: 'prompt'`) and small on purpose: it precaches the app shell and answers navigations with `index.html`; it has no runtime caching, so no signed-in API response is ever stored, and requests to the API and Supabase never touch it. A new version waits until the person taps "Update", then takes over (`SKIP_WAITING`); the page looks for one when the app returns to the foreground and hourly. Phase 4 adds push handlers to the same file. The footer's build id is a hash of the app's own sources, not the commit: Cloudflare rebuilds on every push to `main`, and a commit id would make each one look like a new version.
- **The API is called with the token in an `Authorization` header** and `cache: 'no-store'`; the typed client is generated from the committed `web/openapi.json` (CI regenerates it and fails on drift).
- **What isn't proven yet:** the service worker's real install and update, iOS Add to Home Screen, and a live sign-in can only be seen at the deployed HTTPS address, on the phone. The in-app browser used for testing can't register service workers, so the built worker was exercised in a sandbox (what it handles, what it ignores) and the app was driven against a stand-in for the API and Supabase.
