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
