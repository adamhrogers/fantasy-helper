# ADR 0022: One box score for every league; leagues differ only in their rules

**Status:** Accepted · 2026-11-06

## Decision
- **One source of truth for what happened in a game.** Box scores come from one source, ESPN's public per-game stats, and are stored once in `player_game_stats`, keyed by canonical player and game. They're never keyed by league or platform. Every league on every platform reads the same rows.
- **Each league scores them with its own rules, read from its own settings:** weights, threshold bonuses, categories. Nothing is assumed from the platform. If a league has bonuses, they're that league's settings, not a platform feature.
- **Settings are mapped onto box score stats exactly:**
  - A setting that is an exact combination of box score stats (missed shots = attempts − makes) becomes weights on its parts.
  - A setting with no box score stat behind it (plus/minus, team wins) is reported as unmapped and fails ingest loudly. It's never silently scored as 0.
- **Platform-reported points are only checks.** ESPN's `appliedTotal` and Sleeper's `players_points` are used to check our numbers (and, for lock-in, to read lock state), never as an input to scoring.

## Why
Calls, grades and trajectories compare across leagues and platforms. The same player's game has to be the same facts everywhere; only its worth differs, by each league's rules.

**Checked on 2026-11-06** (docs/spikes/m2-scoring-check.md):
- **Complete:** every one of the 123 games played so far has box scores for both teams.
- **Consistent:** zero violations in 2,749 played rows (points from shots, rebounds from offensive plus defensive, makes within attempts).
- **ESPN:** our season totals match ESPN's own for all 155 rostered players in the points league.
- **Sleeper:** 405 of 417 player-weeks match. Scored from Sleeper's own stats, our code matches all 417. The 12 differ only because the two stat feeds credit a single stat differently: 2,400 of 2,431 player-games are identical, and the other 31 each differ by one in one stat.

## Rejected
- **Scoring each platform's leagues from that platform's own stats:** two truths, so the same game is worth different things in different leagues, and grading becomes inconsistent. The cost of one truth is that a Sleeper league's score can differ from what Sleeper shows by a point or two in about 1% of player-games. Lock-or-pass cards flag near-ties.
- **ESPN's per-game box score pages** (site API, one request per game): they also cover players outside ESPN Fantasy's player pool. That was 14 player-games in three weeks, all deep-bench and on no roster in our leagues. This stays the fallback if a rostered player ever lacks box scores.

## Consequences
- Ingest already warns when a rostered player doesn't resolve to an ESPN player. The game-log pull is flagged if it fills its size limit.
- A daily reconciliation (ESPN season totals and Sleeper weekly points against our scores) arrives with lock-in, which uses the same matching to read lock state.
- New stats a league might score go into `domain.stats.Stat` and a box score column together.
