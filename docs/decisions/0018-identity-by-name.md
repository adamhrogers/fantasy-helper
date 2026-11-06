# ADR 0018: Match players across platforms by name, conservatively

**Status:** Accepted · 2026-11-06

## Decision
ESPN players become canonical players. Sleeper players link to them by normalized name (accents, punctuation and Jr./III removed): same name and team, or a name that's unique on both platforms. Anything ambiguous stays unmatched, gets a provisional canonical player, and is reported. `identity/overrides.py` holds manual links; a provisional player is folded into the right one when a link appears. Stored transaction items keep each platform's own IDs, so a corrected link never rewrites history.

## Why
The M1 spike assumed Sleeper's `espn_id` would make this a lookup, but **Sleeper fills no ESPN or Yahoo IDs for NBA players** (0 of 2,176). Name matching is the standard alternative; refusing to guess keeps a wrong link from silently mixing two players' data. First live run: **142 of 142 Sleeper-rostered players linked**.

## Rejected
- **Fuzzy matching:** near-miss names are exactly the cases where two different players get merged.
- **Matching on Sportradar or RotoWire IDs:** Sleeper has them, ESPN doesn't expose them.
