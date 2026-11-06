# ADR 0018: Match players across platforms by name and birth date, conservatively

**Status:** Accepted · 2026-11-06 (birth dates added the same day, before merge, after Adam asked how trades are handled)

## Decision
ESPN Fantasy players become canonical players. Each Sleeper player links to one, in this order:
1. **override**: a manual link in `identity/overrides.py`
2. **name_birthdate**: same normalized name and a birth date within one day. **This doesn't depend on the team, so it survives trades**, even for common names.
3. **name_team**: same name and the same NBA team, exactly one candidate
4. **name_unique**: a name that's unique on both platforms

Anything ambiguous stays unmatched, gets a provisional canonical player and is reported; it's folded into the right player if a link appears later.

**Once made, a link is stored by platform IDs** (Sleeper ID ↔ ESPN ID). Later trades, team changes and name changes never break it, and every ingest refreshes each player's team. Stored transaction items keep platform IDs, so a corrected link never rewrites history.

**Birth dates** come from ESPN's public sports API athlete list (a morning snapshot, `espn/athletes`) and from Sleeper's player list. They're **evidence, never a veto**: linked pairs whose birth dates disagree by more than a day are counted in the ingest report, not unlinked.

## Why
- **Sleeper fills no ESPN or Yahoo IDs for NBA players** (0 of 2,176), so matching on IDs is impossible.
- **Name plus team alone breaks around trades**: when one platform updates a traded player's team before the other, and his name isn't unique, he can't be linked. Name plus birth date fixes that.
- **Birth dates are imperfect across platforms.** On 2026-11-06, of 623 linked pairs with both dates, 618 agreed; 3 were off by exactly one day (a time-zone artifact, hence the tolerance); 2 had a wrong month on one side (Jazian Gortman, Zach Edey). All five were the same person, so a mismatch must not block a link.
- Results on 2026-11-06: **142 of 142 Sleeper-rostered players** linked to ESPN; 1,031 links overall. The 821 still unmatched aren't in ESPN Fantasy's player pool at all (98 of them are in ESPN's general athlete list), so there's nothing to link them to; they stay Sleeper-only with Sleeper projections.

## Rejected
- **Fuzzy name matching:** near-miss names are exactly where two different players get merged.
- **Birth date as a veto:** would have broken five correct links.
- **Matching on Sportradar or RotoWire IDs:** Sleeper has them, ESPN doesn't expose them.
