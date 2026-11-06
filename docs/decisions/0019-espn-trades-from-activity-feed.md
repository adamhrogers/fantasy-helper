# ADR 0019: Every league trade comes from ESPN's activity feed

**Status:** Accepted · 2026-11-06 (for the trade tracker, P9)

## Decision
The trade tracker reads ESPN trades from the league activity feed (`/communication/?view=kona_league_communication`, message type 244), which lists every trade with its players, both teams and the date. The transactions view adds proposals, declines and counter-offers for the owner's own team.

## Why
An accepted trade carries no players; they're on the proposal, and **ESPN shows proposals only to the teams involved**. In the points league, the 6 trades involving Adam's team resolved and the 4 between other teams didn't. The spec's tracker grades every league trade.

## Consequences
The feed keeps the whole season, so it doesn't need daily snapshots. Feed messages name the member who posted them, so they're redacted before any fixture is recorded.
