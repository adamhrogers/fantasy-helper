# ADR 0013: Gradable takes: direction, horizon and conviction

**Status:** Accepted · 2026-10-19

## Decision
A take records a direction (will beat or trail projections), a horizon (7, 14 or 30 days), a conviction (1–5) and a one-line text. Takes are immutable; an edit creates a new take that supersedes the old one.

## Why
Code can grade every take against the projection snapshot from when it was made, without an LLM interpreting free text.

## Rejected
- **Free text only:** grading would depend on an LLM's reading of the claim.
