# M1 spike: what ESPN and Sleeper actually expose

**Date:** 2026-11-06 (NBA scoring period 18; Sleeper week 3) · **Leagues tested:** an ESPN head-to-head points league, an ESPN 9-category league and a Sleeper points league, each with 10 teams. Leagues are named here by format only, because the repo is public.

Raw responses were inspected for structure only. Recorded fixtures come from the redacting recorder (ADR 0012).

## The six roadmap questions

| # | Question | Answer |
|---|---|---|
| Q1 | **Which per-stat projections does each platform expose?** *(blocks M2)* | **Both: per stat, free, no login.** **ESPN:** a season projection per player, with totals and per-game averages for 31 stat IDs, on the public `kona_player_info` view (`leaguedefaults`). **No per-day projections**: tried in league context and with the projection filters (`filterStatsForSourceIds`, `filterStatsForSplitTypeIds`, `filterStatsForCurrentSeasonScoringPeriodId`), which all returned nothing. **Sleeper:** season (`api.sleeper.com/projections/nba/{season}`) **and per-game weekly** (`…/{season}/{week}`) projections with pts, reb, ast, stl, blk, to, fgm/fga, ftm/fta, tpm/tpa, dd and more. Undocumented, but public. **The scoring layer is unblocked; per-game projections come from Sleeper.** |
| Q2 | **Where do per-game logs come from for players who aren't rostered?** | **ESPN**, for every player: actual stats per game (`statSourceId 0`, `statSplitTypeId 5`, one entry per scoring period) on the same public view. Also Sleeper's `stats/nba/{season}/{week}` (per game, even split by half). |
| Q3 | **Does ESPN expose league scoring weights through the API?** | **Yes**: `settings.scoringSettings.scoringItems` (statId → points; categories marked with `isReverseItem` for turnovers). Sleeper too: `scoring_settings`, including **threshold bonuses** (`bonus_pt_40p`, `bonus_pt_50p`, …). |
| Q4 | **Are Sleeper lock actions visible through the API?** | **No.** Matchups carry only weekly `players_points` / `starters_points`, and transactions carry adds and drops. Nothing records which game was locked. **The test league is lock-in** (confirmed by Adam on 2026-11-06), so **M5 adds the one-tap "I locked / I passed" confirmation.** Its `settings.game_mode` is `1`; the adapter maps that to lock-in, an inference from this one league to recheck if another league disagrees. |
| Q5 | **Which free source can supply injury and news text?** | **Injury status:** both. ESPN `injuryStatus` on every player; Sleeper `injury_status`, `injury_notes`, `injury_start_date`, `news_updated`. **News text:** ESPN's player news endpoint (`site.api.espn.com/apis/fantasy/v3/games/fba/news/players`) is the candidate, to be evaluated in M4. |
| Q6 | **How quickly do rest-of-season projections react to injuries?** | **Pending.** It needs several days of snapshots, which started on 2026-11-06. Compare `injuryStatus` changes against projection changes day over day. |

## Other findings that shape the design

- **Player identity is mostly a lookup.** Sleeper's player list includes `espn_id`, `yahoo_id`, `sportradar_id`, `rotowire_id` and more.
- **Every league trade needs ESPN's activity feed.** An accepted trade (`TRADE_ACCEPT`) has no items: its players are on the proposal it points to (`relatedTransactionId`). ESPN shows proposals **only to the teams involved**. In the points league, the 6 trades involving Adam's team resolve, and the 4 between other teams don't. The league **activity feed** (`/communication/?view=kona_league_communication`, topic type `ACTIVITY_TRANSACTIONS`, message type 244 = traded) lists every trade with the player (`targetId`), both teams (`from` / `to`) and the date, and it keeps the whole season, so it can be fetched any time. **Plan:** the activity feed is the source for every league trade (built with the trade tracker, P9); the transactions view adds proposals, declines and counter-offers for Adam's own team. Feed messages carry the member who posted them, so they're redacted before recording.
- **Full transaction history from ESPN:** `mTransactions2`, queried one scoring period at a time with a type filter (`FREEAGENT`, `WAIVER`, `TRADE_ACCEPT`, …). The points league has 118 so far, including 10 accepted trades with every player moved. That supports the trade tracker's backfill to opening night. The default `mTransactions2` call returns only recent lineup moves.
- **ESPN league settings:**
  - lineups lock per game (`INDIVIDUAL_GAME`), so lineups are daily
  - roster slots come from `lineupSlotCounts` (slot IDs: 0–6 positions, 11 UTIL, 12 bench, 13 IR)
  - add limits are a **per-day rate** (`matchupAcquisitionLimit` with `matchupLimitPerScoringPeriod`): 0.571 a day (4 per 7-day week) in the points league, 1.0 in the categories league
- **Sleeper league settings:** roster positions `PG, SG, G, SF, PF, F, C, 3 UTIL, 3 BN` plus 2 reserve. Transactions come per round (`/transactions/{week}`).
- **Correction to the spec:** the Sleeper league scores an assist at **2**, not 4. Weights are always read from league settings, never assumed.
- **Snapshot size:** the full morning set is about 1 MB a day gzipped. ESPN projects about 350 of 1,100 active players; the rest still carry injury status and ownership.

## Domain changes this requires (tables v1)

1. `ScoringRules` gains **threshold bonuses** (stat, threshold, points).
2. `AcquisitionLimits` gains a **per-day rate** next to the fixed per-matchup and per-season limits.
3. Roster slots are normalized across platforms (`BE` / `BN` → bench, ESPN slot IDs → names).
4. A stat-code table per platform (ESPN numeric IDs, Sleeper keys like `tpm`, `fgmi`) maps into the domain's `Stat`.
