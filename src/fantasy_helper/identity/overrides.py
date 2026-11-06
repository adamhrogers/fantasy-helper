"""Manual identity links for players the matcher can't tie by name (nicknames, name changes).

Sleeper player ID -> ESPN player ID. Both are public IDs. Add an entry when `fh ingest`
reports a rostered Sleeper player as unmatched, with a comment naming the player.
"""

SLEEPER_TO_ESPN: dict[str, str] = {}
