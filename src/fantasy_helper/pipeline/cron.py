"""Which scheduled job a GitHub Actions cron entry stands for, in Eastern time (ADR 0023).

GitHub's cron runs in UTC, but the jobs follow the NBA's clock (America/New_York), which moves
by an hour twice a year, once inside the season (2027-03-14). So every job has two entries: one
for Eastern Standard Time (UTC-5) and one for Eastern Daylight Time (UTC-4). When an entry
fires, the job runs only if New York's offset at that moment matches the entry's; the other
entry exits at once. Each job then runs once a day at the same local time all year, and a
late start (GitHub can run scheduled jobs minutes late) doesn't change which entry runs.

Minutes are off the hour on purpose: GitHub delays scheduled runs most at the top of the hour.
A test keeps this table and the workflow files in step.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from fantasy_helper.domain.calendar import NBA_TIMEZONE

EST = -5
EDT = -4


@dataclass(frozen=True)
class CronEntry:
    job: str
    utc_offset_hours: int  # the New York offset this entry is for


CRON_ENTRIES: dict[str, CronEntry] = {
    # .github/workflows/snapshot.yml: 06:17 and 17:17 Eastern
    "17 11 * * *": CronEntry("snapshot-morning", EST),
    "17 10 * * *": CronEntry("snapshot-morning", EDT),
    "17 22 * * *": CronEntry("snapshot-evening", EST),
    "17 21 * * *": CronEntry("snapshot-evening", EDT),
}


class UnknownCronError(ValueError):
    pass


def job_for(cron: str, now: datetime) -> str | None:
    """The job this cron entry runs right now, or None if it's the other offset's entry."""
    entry = CRON_ENTRIES.get(" ".join(cron.split()))
    if entry is None:
        raise UnknownCronError(f"no job is scheduled for cron {cron!r} (pipeline/cron.py)")
    offset = now.astimezone(NBA_TIMEZONE).utcoffset()
    return entry.job if offset == timedelta(hours=entry.utc_offset_hours) else None
