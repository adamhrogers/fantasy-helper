# ADR 0014: Timeline graphs computed from stored snapshots

**Status:** Accepted · 2026-10-19

## Decision
Each night, every open take, call and trade gets an `EvaluationPoint`: points realized so far plus points still projected, using that day's projections. At the end of the window nothing is left to project, so the last point equals the final grade. Points are computed from stored snapshots and can be rebuilt or backfilled at any time.

## Why
Adam wants to see how a decision looked over time: a trade that goes bad on a re-injury, or a take that looked wrong and came good. This approach costs little because it reuses the snapshots grading already needs.

## Rejected
- **Estimating return dates ourselves:** injury timelines come from providers' rest-of-season projections, not from us.

## Consequences
**The daily snapshot is the part that can't be recreated**, so snapshot recording ships first in M1.
