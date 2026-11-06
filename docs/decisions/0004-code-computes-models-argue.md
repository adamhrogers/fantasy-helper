# ADR 0004: Code computes, models argue

**Status:** Accepted · 2026-10-16

## Decision
The engine computes every number: league scoring, replacement level, games per week, the streaming plan, win probability, lock-in values and baselines. It also generates the **candidate moves** with their expected values. Models read a compact **FactSheet** of those facts, then select, rank, explain and challenge. A move outside the candidate set must be scored by code through the `evaluate_move` tool. Guardrails check every draft: schema validation, numeric grounding (every number in the prose exists in the FactSheet or a tool result) and the candidate-set check. A draft that fails twice is replaced by the engine-only report.

## Why
The spec's rule is that models never produce a stat. Making the engine the only source of numbers, and checking drafts mechanically, enforces it. Compact FactSheets also keep token costs low.

## Rejected
- **Letting agents query raw data and compute:** unverifiable numbers and higher token use.
