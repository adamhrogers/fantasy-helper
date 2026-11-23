"""Render ReportDocuments as one local HTML page: what `fh report open` shows, drawn like the
phone app's cards (title, body, bullets, reason), with the day's first game at the top."""

import html
from collections.abc import Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fantasy_helper.domain.calendar import NBA_TIMEZONE
from fantasy_helper.domain.reports import CardKind, GameDay, ReportDocument

_STYLE = """
:root { --bg: #f6f6f4; --card: #fff; --ink: #1d1d1b; --muted: #6b6b66; --lock: #1f7a4d;
        --pass: #9a5b00; --line: #e4e4df; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #161615; --card: #22221f; --ink: #ecece8; --muted: #a3a39c; --lock: #5fcf95;
          --pass: #f0b45a; --line: #33332f; }
}
body { margin: 0; background: var(--bg); color: var(--ink);
       font: 16px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
main { max-width: 640px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 22px; margin: 0 0 4px; } .sub { color: var(--muted); margin: 0 0 20px; }
h2 { font-size: 15px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em;
     margin: 28px 0 10px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
        padding: 14px 16px; margin: 0 0 12px; }
.card h3 { margin: 0 0 6px; font-size: 17px; }
.card.lock h3 { color: var(--lock); } .card.pass h3 { color: var(--pass); }
.card p { margin: 0 0 6px; } .why { color: var(--muted); font-size: 14px; margin: 0; }
.card ul { margin: 0 0 6px; padding-left: 20px; } .card li { margin: 0 0 2px; }
.games { margin: 0 0 20px; font-weight: 600; }
"""


def first_game(games: GameDay, time_zone: str) -> str:
    """ "First game on Sun Nov 15: 7:00 PM EST" in the user's zone, or that there are none. The
    page can be opened for any saved date, so it names the day rather than saying "today"."""
    day = f"{games.day:%a %b} {games.day.day}"
    if games.first_tip_utc is None:
        return f"No NBA games on {day}."
    try:
        zone = ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = NBA_TIMEZONE
    local = games.first_tip_utc.astimezone(zone)
    return f"First game on {day}: {local.strftime('%I:%M %p').lstrip('0')} {local:%Z}"


def render(
    reports: Sequence[ReportDocument],
    *,
    title: str,
    league_names: dict[str, str],
    games: GameDay | None = None,
    time_zone: str = NBA_TIMEZONE.key,
) -> str:
    sections = []
    for report in reports:
        cards = []
        for card in report.cards:
            tone = ""
            if card.kind is CardKind.LOCK_OR_PASS:
                tone = " lock" if card.title.startswith("Lock") else " pass"
            body = f"<p>{html.escape(card.body)}</p>" if card.body else ""
            items = (
                "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in card.items) + "</ul>"
                if card.items
                else ""
            )
            why = f'<p class="why">{html.escape(card.reason)}</p>' if card.reason else ""
            heading = f"<h3>{html.escape(card.title)}</h3>"
            cards.append(f'<div class="card{tone}">{heading}{body}{items}{why}</div>')
        name = league_names.get(str(report.league_id), "League")
        sections.append(f"<h2>{html.escape(name)}</h2>" + "".join(cards))
    first = reports[0] if reports else None
    subtitle = (
        f"{first.for_date:%A, %B %d} · written by code (engine-only)" if first else "No reports"
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head><body><main>"
        f'<h1>{html.escape(title)}</h1><p class="sub">{html.escape(subtitle)}</p>'
        + (f'<p class="games">{html.escape(first_game(games, time_zone))}</p>' if games else "")
        + "".join(sections)
        + "</main></body></html>"
    )
