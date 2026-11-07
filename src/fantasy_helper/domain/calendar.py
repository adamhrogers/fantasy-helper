"""NBA season arithmetic. Pure functions: no clock, no I/O."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

NBA_TIMEZONE = ZoneInfo("America/New_York")
"""The league's own calendar: a game's date is its tip-off date in New York."""


def season_end_year(on: date) -> int:
    """The season a date belongs to, named by the year it ends: 2027 for 2026-27.

    The domain names seasons this way (ESPN's convention). Training camp opens in late
    September, so August onward counts as the next season.
    """
    return on.year + 1 if on.month >= 8 else on.year


def season_start_year(on: date) -> int:
    """Sleeper's label for the same season: 2026 for 2026-27."""
    return season_end_year(on) - 1


def nba_game_date(tip_utc: datetime) -> date:
    """The date a game is listed under: its tip-off in Eastern time (a 10:30 p.m. ET tip is
    already tomorrow in UTC)."""
    return tip_utc.astimezone(NBA_TIMEZONE).date()


def sleeper_week_bounds(season_start: date, week: int) -> tuple[date, date]:
    """A Sleeper NBA week's first and last day. Weeks run Monday to Sunday; week 1 runs from
    opening night to the first Sunday (checked against Sleeper's weekly points, 2026-11-06)."""
    if week < 1:
        raise ValueError("Sleeper weeks start at 1")
    first_sunday = season_start + timedelta(days=(6 - season_start.weekday()) % 7)
    if week == 1:
        return season_start, first_sunday
    start = first_sunday + timedelta(days=1 + 7 * (week - 2))
    return start, start + timedelta(days=6)


def sleeper_week_of(season_start: date, day: date) -> int:
    """The Sleeper week a date falls in (1 for any day up to the first Sunday)."""
    first_sunday = season_start + timedelta(days=(6 - season_start.weekday()) % 7)
    if day <= first_sunday:
        return 1
    return 2 + (day - first_sunday - timedelta(days=1)).days // 7
