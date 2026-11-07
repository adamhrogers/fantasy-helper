"""GitHub's cron is UTC; the jobs follow New York time, across both DST changes."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fantasy_helper.pipeline.cron import CRON_ENTRIES, UnknownCronError, job_for

WORKFLOWS = Path(__file__).parents[2] / ".github" / "workflows"


def at(year: int, month: int, day: int, hour: int, minute: int = 17) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    ("cron", "now", "job"),
    [
        # Winter (EST, UTC-5): the 11:17 UTC entry is 06:17 in New York.
        ("17 11 * * *", at(2026, 11, 7, 11), "snapshot-morning"),
        ("17 10 * * *", at(2026, 11, 7, 10), None),  # 05:17 EST: the summer entry sits out
        ("17 22 * * *", at(2026, 11, 7, 22), "snapshot-evening"),
        ("17 21 * * *", at(2026, 11, 7, 21), None),
        # Summer (EDT, UTC-4), from 2027-03-14: the 10:17 UTC entry is 06:17 in New York.
        ("17 10 * * *", at(2027, 3, 15, 10), "snapshot-morning"),
        ("17 11 * * *", at(2027, 3, 15, 11), None),
        ("17 21 * * *", at(2027, 3, 15, 21), "snapshot-evening"),
        ("17 22 * * *", at(2027, 3, 15, 22), None),
        # The change days themselves: clocks move at 2 a.m., hours before either entry.
        ("17 10 * * *", at(2027, 3, 14, 10), "snapshot-morning"),
        ("17 11 * * *", at(2026, 11, 1, 11), "snapshot-morning"),
    ],
)
def test_one_entry_runs_each_day(cron: str, now: datetime, job: str | None) -> None:
    assert job_for(cron, now) == job


def test_a_late_start_doesnt_change_which_entry_runs() -> None:
    """GitHub can start a scheduled run late; the entry decides, not the clock reading."""
    assert job_for("17 11 * * *", at(2026, 11, 7, 12, 40)) == "snapshot-morning"
    assert job_for("17 10 * * *", at(2026, 11, 7, 11, 5)) is None


def test_every_job_runs_once_a_day_all_year() -> None:
    for month in range(1, 13):
        day = datetime(2027, month, 15, tzinfo=UTC)
        ran = [
            job
            for cron in CRON_ENTRIES
            if (job := job_for(cron, day.replace(hour=int(cron.split()[1])))) is not None
        ]
        assert sorted(ran) == sorted({e.job for e in CRON_ENTRIES.values()}), month


def test_unknown_entries_are_an_error() -> None:
    with pytest.raises(UnknownCronError):
        job_for("0 9 * * *", at(2026, 11, 7, 9))


def test_spacing_is_normalized() -> None:
    assert job_for(" 17  11 * * * ", at(2026, 11, 7, 11)) == "snapshot-morning"


def test_the_workflows_and_the_table_agree() -> None:
    in_workflows = {
        match
        for path in WORKFLOWS.glob("*.yml")
        for match in re.findall(r'-\s*cron:\s*"([^"]+)"', path.read_text())
    }
    assert in_workflows == set(CRON_ENTRIES)
