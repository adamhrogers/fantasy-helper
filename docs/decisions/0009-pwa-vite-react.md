# ADR 0009: Phone app as a PWA built with Vite, React and TypeScript

**Status:** Accepted · 2026-10-19

## Decision
An installable PWA (Vite, React, TypeScript, vite-plugin-pwa) with Web Push, hosted on Cloudflare Pages. There is no native app until the beta proves demand (spec).

## Why
It installs to the home screen, sends push on iPhone and Android, and updates instantly while the product changes every week. React has the largest ecosystem for swipeable cards and charts (Recharts for the timeline graphs).

## Rejected
- **SvelteKit:** lighter, but a smaller ecosystem.
- **Native app:** App Store friction before the product is proven.

## Consequences
iOS push requires adding the app to the Home Screen (iOS 16.4+), so beta onboarding includes that step.
