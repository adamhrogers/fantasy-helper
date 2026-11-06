"""Name normalization for matching players across platforms."""

import re
import unicodedata

_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})


def normalize_name(name: str) -> str:
    """'Moussa Diabaté' -> 'moussa diabate'; 'P.J. Washington Jr.' -> 'pj washington'.

    Accents are stripped, periods and apostrophes removed (so "P.J." matches "PJ" and
    "D'Angelo" matches "DAngelo"), other punctuation becomes a space, and generational
    suffixes are dropped.
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    cleaned = re.sub("[.'\u2019`]", "", ascii_name.lower())  # \u2019 is the curly apostrophe
    tokens = re.sub(r"[^a-z0-9]+", " ", cleaned).split()
    while len(tokens) > 1 and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)
