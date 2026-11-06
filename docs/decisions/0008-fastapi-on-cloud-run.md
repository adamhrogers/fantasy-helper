# ADR 0008: FastAPI on Cloud Run as the phone app's backend

**Status:** Accepted · 2026-10-19

## Decision
The PWA talks only to a FastAPI backend-for-frontend, deployed on Google Cloud Run from M3. One container image serves both the API and, if needed, the scheduled jobs.

## Why
A static PWA on Cloudflare Pages still has to save takes, feedback and push subscriptions, and friends need auth. That's a gap in the spec. FastAPI shares the Pydantic domain models, and its OpenAPI schema generates the TypeScript client. Cloud Run's free tier scales to zero, and the spec already names it as the nightly job's fallback host.

## Rejected
- **Cloudflare Workers:** splits the backend across two languages.
- **Supabase PostgREST from the client:** spreads authorization across RLS policies and the frontend instead of one enforcement point.
