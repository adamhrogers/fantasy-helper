"""NBA season arithmetic. Pure functions: no clock, no I/O."""

from datetime import date, datetime
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
