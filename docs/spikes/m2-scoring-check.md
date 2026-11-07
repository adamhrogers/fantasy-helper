# M2 check: one box score for every league, and our scoring against the platforms

**Date:** 2026-11-06 (NBA scoring period 18; Sleeper week 3). Counts only; leagues are named by format, because the repo is public. Scripts ran from a scratch folder against the dev database and the raw store. The design this supports is ADR 0022.

## The box score: one source of truth

- **The source:** ESPN's public `kona_player_info` view with `filterStatsForTopScoringPeriodIds.value = N`.
  - Per-game entries are `statSourceId 0, statSplitTypeId 5`, keyed by the ESPN game ID (`externalId`), with the player's team for that game (`proTeamId`).
  - **N counts each player's most recent entries, across seasons, not scoring days.** The mapper keeps only the requested season.
  - The pull covers ESPN's whole player pool: 1,100 players against a limit of 1,200, and a pull that fills the limit is flagged.
- **Games sat out are rows,** with no games-played stat and no stats (4,186 of 6,935 on 2026-11-06), stored with `played = false`.
- **Stored once:** in `player_game_stats`, keyed by canonical player and game, with every box score stat a league could score: the counting stats, double-, triple- and quadruple-doubles, technical and flagrant fouls, ejections, disqualifications and minutes.
- **Size and recovery:** the daily pull (`espn.game_logs`, N = 5, morning set) is about 0.4 MB gzipped. The backfill (`fh snapshot --only espn.game_logs --game-log-depth 30`) is about 0.9 MB. A missed day is recovered by a deeper pull.

| Check (dev database) | Result |
|---|---|
| Games played so far with box scores for both teams | 123 of 123 (at least 6 players a team) |
| Rows whose team isn't one of the game's two teams | 0 |
| Played rows breaking PTS = 2·FGM + 3PM + FTM, REB = OREB + DREB, makes ≤ attempts, 3PM ≤ FGM, or with a missing core stat | 0 of 2,749 |
| Rows for games sat out that carry any stat | 0 |
| Rostered players with box score rows, per league | 147/147 and 155/155 (ESPN); 142/143 (Sleeper: the one without is on no NBA team this season) |
| Players rostered in several leagues | 143, all reading the same rows (box scores have no league or platform key) |

## The same box scores against the platforms' own numbers

- **ESPN points league:** for each of the 155 rostered players, the sum of our per-game points this season equals ESPN's own season `appliedTotal`. **155 of 155, exact.**
- **Sleeper points league (lock-in):** each player's weekly `players_points` is one game, so it should equal our score for one of his games that week. Sleeper weeks run Monday to Sunday: week 1 was Tuesday 10-20 to Sunday 10-25.
  - **405 of 417 player-weeks match.**
  - **Scored from Sleeper's own per-game stats** (`api.sleeper.com/stats/nba/{season}/{week}`, explored, not wired in), our code matches **all 12** of the others: **417 of 417.** The scoring code and the league's settings are right; the 12 come from the stat feed.
- **ESPN against Sleeper's stat feed:** 2,400 of 2,431 player-games are identical. Each of the other 31 differs by one in one stat (an assist, rebound, turnover, steal, block or shot attempt credited differently). Sleeper also lists 14 player-games, by deep-bench players outside ESPN Fantasy's pool and on no roster in our leagues, that ESPN's fantasy feed doesn't have.
- **Bonuses:** threshold bonuses are league settings, and a game that reaches two thresholds on one stat earns both. That shows in the Sleeper league's numbers, whose settings have two points thresholds.

## Mapping each league's settings onto the box score

- Both adapters read every scoring setting a league has.
- **Missed shots** (ESPN 23/24/25; Sleeper `fgmi`, `ftmi`, `tpmi`) become weights on attempts and makes, exactly.
- **Fouls, ejections, disqualifications, quadruple-doubles** (ESPN 7, 8, 10, 12, 39; Sleeper `tf`, `ff`) are box score stats.
- **A setting with no box score stat behind it** (Sleeper `plus_minus`, ESPN team wins) is reported as unmapped, and ingest fails loudly rather than scoring it as 0.
- Contract tests cover all of this with settings Adam's leagues don't use.

## Lock state: Sleeper's weekly points don't show locks (corrected 2026-11-06, evening)

**First reading (afternoon):**
- If a player's weekly points equal an **earlier** game, he looked locked on that game.
- If they equal his **latest** game, he looked open.

Completed weeks fit that, and so did week 3 at the time: 115 player-weeks showing their latest game, and 20 an earlier one.

**Then the numbers moved.** Between 7:32 and 7:52 pm ET on Friday, three of Adam's starters changed from their Monday game's score to their Wednesday game's score (56.5 → 29.5, 45.5 → 30.0, 36.0 → 37.5). A locked score can't change. A fourth stayed on Monday's 59.5. Sleeper's matchup payload has no lock field at all (`players`, `starters`, `players_points`, `starters_points`, `points`, `custom_points`). So its displayed number seems to roll at some point in the day, perhaps at the first tip, and before that it can show an older game.

**Tested against Adam's actual locks (2026-11-06, evening).** Adam listed his week-3 locks: 7 locked starters, 3 open starters, 3 bench and 2 IR. Each player's games were scored from Sleeper's own stats (so feed differences can't blur it) and lined up against 13 pulls of the matchup that day. **No rule fits:**
- **Numbers flip with nothing happening.** Values flipped back and forth between pulls minutes apart, for locked starters, open starters and bench players alike. One locked starter went 56.5 → 29.5 → 56.5 within three minutes. Another switched four times with no new game. Two bench players flipped between their last game and 0.
- **The lineup isn't the cause.** It changed once that day, and most flips happened with no lineup change.
- **The latest pull contradicts the locks.** It showed a game in progress for a locked starter, and an earlier game for an open one.

**What this changes:**
- **Sleeper's weekly number is not a lock signal, not even a hint.** It's kept in each call's detail for later analysis, never shown and never used as a filter.
- **Every open decision gets a card.** The report says plainly: "The app can't see your locks yet: skip any player you've already locked."
- **Locks come from the user:** a per-week lock flag on the roster, set with one tap in the phone app (M3/M5), or a quick command until then. That flag also gives grading its follow detection.
- **Completed weeks are still reliable** for reconciliation: the final number is the locked game or the last game.

## Consequences

- **Near-ties:** because our box score is the one truth, a Sleeper score can differ from Sleeper's display by a point or two in about 1% of player-games. Lock-or-pass cards show the margin and flag near-ties; they never switch to a second stat source.
- **Daily reconciliation, built (`pipeline/reconcile.py`):** the comparisons above run every morning in `fh run daily`.
  - ESPN season totals, leaving out players whose game today has tipped.
  - Sleeper completed weeks only.
  - The run warns below 98% (ESPN) or 90% (Sleeper).
  - First live run: ESPN 75/75 (on a game night), Sleeper 271/275.
- **Gotcha:** stored league rules (`league_settings_versions.rules`) include the computed `content_hash`. Drop it before `LeagueRules.model_validate`.
