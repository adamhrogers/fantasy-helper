"""Logging calls in the registry: once, before tip-off, never edited (ADR 0005)."""

from datetime import datetime

from sqlalchemy.orm import Session

from fantasy_helper.domain.recommendations import Recommendation
from fantasy_helper.storage.repositories import recommendations


class TipOffPassedError(ValueError):
    """The game started before the call could be written: it can't be graded fairly."""


def log_call(
    session: Session, rec: Recommendation, dedupe_key: str, *, now: datetime
) -> tuple[Recommendation, bool]:
    """Write a call, or return the one already logged under the same key (False). The domain
    model checks the call was made before tip-off; this checks it's written before tip-off too."""
    if now >= rec.first_tip_utc:
        raise TipOffPassedError("tip-off has passed: the call wasn't logged")
    return recommendations.record(session, rec, dedupe_key)
