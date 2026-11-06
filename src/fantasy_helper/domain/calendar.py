"""NBA season arithmetic. Pure functions: no clock, no I/O."""

from datetime import date


def season_end_year(on: date) -> int:
    """The season a date belongs to, named by the year it ends: 2027 for 2026-27.

    The domain names seasons this way (ESPN's convention). Training camp opens in late
    September, so August onward counts as the next season.
    """
    return on.year + 1 if on.month >= 8 else on.year


def season_start_year(on: date) -> int:
    """Sleeper's label for the same season: 2026 for 2026-27."""
    return season_end_year(on) - 1
