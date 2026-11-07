"""Render ReportDocuments as one local HTML page, until the phone app (M3) shows them."""

import html
from collections.abc import Sequence

from fantasy_helper.domain.reports import CardKind, ReportDocument

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
"""


def render(reports: Sequence[ReportDocument], *, title: str, league_names: dict[str, str]) -> str:
    sections = []
    for report in reports:
        cards = []
        for card in report.cards:
            tone = ""
            if card.kind is CardKind.LOCK_OR_PASS:
                tone = " lock" if card.title.startswith("Lock") else " pass"
            why = f'<p class="why">{html.escape(card.reason)}</p>' if card.reason else ""
            cards.append(
                f'<div class="card{tone}"><h3>{html.escape(card.title)}</h3>'
                f"<p>{html.escape(card.body)}</p>{why}</div>"
            )
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
        + "".join(sections)
        + "</main></body></html>"
    )
