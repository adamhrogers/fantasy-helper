# ADR 0033: The daily report says what happened in every league, in that league's own scoring

**Status:** Accepted · 2026-11-16

## Decision
Every league, whatever its format, gets a daily report with "what happened last night" cards. They are built by code from stored facts, with no model, and a lock-in league keeps its lock-or-pass cards in front of them.

- **One loader, one set of cards.** `facts/daily.py` loads last night for one league into a `DailySheet`; `engine/recap.py` holds the pure math; `reports/recap.py` turns the sheet into cards. The cards are blocks (`matchup`, `my-team`, `standouts`, `pickups`, `surprises`), each skipped when it has nothing to say, so a later "customize my report" can switch them on and off by name.
- **Rule 9 holds.** Every number comes from the one box score per player and game, valued by the league's own settings. A platform's own totals are only ever a check.
  - **Points leagues:** a game is worth `game_points`.
  - **Categories leagues:** a game has no points, so it is valued as an index: the sum over the league's categories of (stat minus a typical game's) divided by the spread, turnovers counting against, and a ratio category measured as makes above what the league's rate would have made on the game's attempts. The index is a straight sum of the stat line, so a projected line is the expected game.
- **The day's totals are counted, not read.** For a league with daily or weekly lineups, a team's total is each starter's game from the lineup stored for that day, summed. A day without a stored lineup gives "unknown", never zero. ESPN's own numbers check ours and the run reports a disagreement: its daily points for a points league, its running category totals for a categories league.
- **Lock-in leagues give no total of ours.** Each starter counts one locked game a week and the app can't see locks, so the card shows last night's game scores before locks next to the platform's own score for the week, labelled as the platform's number. It guesses no locks; the opponent's are not visible either.
- **Expected is what the engine expected before the games.** The projections pulled before the first tip-off and the injury statuses known by then, blended with the player's own earlier games (the projection counts as 10 games of evidence). Nothing reads the future. A player the projection says will not play is "expected to sit", which is different from having no projection at all.
- **The existing card kinds only** (`matchup`, `recap`, `league_moves`). A new kind would change the API's schema for no gain.
- **No migration.** Everything reads tables that exist. The one new stored fact is ESPN's per-category running scores in the matchup row's existing `detail` column. Ingest also fetches yesterday's lineup every morning (on a Monday that is the Sunday that closed the week before), because the day's total needs the lineup the team had.
- **Failures stay apart.** Each league's lock calls and its recap run in their own savepoints. A recap that breaks never costs the lock calls (which have deadlines), and a lock part that breaks still gives the recap plus a card saying the locks weren't built, so a missing card is never read as "nothing to decide".

## Why
Adam's point (2026-11-16): the morning report should say what happened in every league, not only give lock calls for the one lock-in league. The engine already has everything needed (box scores, stored lineups, projections, transactions), and the cards are useful on their own, before M4's brain writes around them. Building them as engine, facts and reports work means they touch nothing the API or the app does, so they run as a lane beside M3's later phases.

## Rejected
- **Reading each platform's own daily numbers for the matchup.** Two truths per league, and the platform's numbers differ by league and format. ESPN's daily totals are used only to check ours, as banked points already are (`pipeline/weekly.py`).
- **A points total for a lock-in league.** It would be a guess at locks the app can't see. Lock-in v2 (matchup-aware risk) and the user-set "I locked" button are separate work.
- **New card kinds or a new table.** More schema for the app to follow, with no new information.
- **Scoring categories games by raw counts.** A rebound and a point are not worth the same in a category league, and turnovers count the other way; the standard-deviation index keeps a league's own categories in play without inventing weights.

## Consequences
- The daily run's summary gains counts only (ADR 0011): cards built, and how many of last night's totals match the platforms'. A disagreement is a warning ("check the stored lineups and box scores"); it is never silent.
- A matchup period that began before this calendar week (the All-Star break) has no matchup card until periods are mapped properly, as the weekly overview does.
- Not checked yet: Sleeper leagues' totals (nothing of ours to check), and ESPN's raw per-stat running totals for a points league (they arrive in the same field and could check box scores directly).
