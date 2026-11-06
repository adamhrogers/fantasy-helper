# ADR 0010: Defer the Batch API

**Status:** Accepted · 2026-10-25 (changed from the spec)

## Decision
The nightly run uses the standard Messages API. The LLM gateway exposes an executor interface so batch execution can be added in one place. Revisit in M8 with cost data from the run log.

## Why
At this scale a daily report costs cents per league, so Batch's 50% discount saves cents a day. Its costs are real, though: an asynchronous window that fights the 8 a.m. deadline, multi-agent debate rounds that each become a batch stage, and model calls that LangSmith no longer traces automatically.

## Rejected
- **Batch from M4 (the spec's plan):** complexity and lost tracing for negligible savings now.
