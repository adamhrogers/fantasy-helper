# ADR 0005: Append-only evidence: versioned inputs, an insert-only registry and a tip-off guard

**Status:** Accepted · 2026-10-16

## Decision
Projections are stored as immutable daily snapshots, league settings as versions keyed by a content hash, and takes as immutable records (an edit supersedes). Every recommendation is inserted once, before the first relevant tip-off, and never edited; the domain model rejects a call whose `created_at` isn't before `first_tip_utc`. Grades are written to a separate table when the outcome window closes.

## Why
The spec's grading rules: log before the outcome, grade against the same snapshot, and never edit afterward. Without this the season produces no trustworthy evidence.

## Rejected
- **Updating rows in place:** loses the inputs a call actually saw.
