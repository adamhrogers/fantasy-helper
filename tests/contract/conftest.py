"""Contract tests: adapters' mappers against real responses recorded by `fh fixtures record`.

The expected values below were checked by hand against the live leagues on 2026-11-06. If the
fixtures are re-recorded, update the counts in the same change.
"""

import json
from pathlib import Path
from typing import Any

RECORDED = Path(__file__).resolve().parents[1] / "fixtures" / "recorded"


def recorded(path: str) -> Any:
    return json.loads((RECORDED / path).read_text(encoding="utf-8"))
