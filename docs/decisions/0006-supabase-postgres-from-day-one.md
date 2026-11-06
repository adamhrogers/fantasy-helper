# ADR 0006: Supabase Postgres from day one

**Status:** Accepted · 2026-10-19 (changed from the spec)

## Decision
Use Supabase Postgres in every environment: a dev project now and a prod project from M3. CI runs tests against a Postgres 17 service container. Migrations are Alembic, and `alembic check` in CI fails if the models and migrations drift apart.

## Why
The spec starts on SQLite and moves to hosted infrastructure before the beta. Leagues and encrypted credentials live in the database from M1 (ADR 0007), and GitHub Actions runners don't keep files between runs, so scheduled jobs need a hosted database anyway. Starting on Postgres removes a migration from the busiest stretch. Supabase bundles Auth, which the phone app needs from M3.

## Rejected
- **SQLite until M3:** a dialect migration in the heaviest week.
- **Neon:** no bundled auth.

## Consequences
Connect through Supabase's **session pooler**: the direct connection is IPv6-only, and GitHub runners don't have IPv6. psycopg never prepares statements, so the transaction pooler works too. The free tier pauses after 7 idle days, which the nightly job prevents.
