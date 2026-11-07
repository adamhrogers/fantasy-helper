"""The registry's storage: insert once, never update (ADR 0005)."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from fantasy_helper.domain.recommendations import Recommendation
from fantasy_helper.storage.tables import RecommendationRow


def record(session: Session, rec: Recommendation, dedupe_key: str) -> tuple[Recommendation, bool]:
    """Log a call. If the same call (same league and key) is already logged, the logged one is
    returned unchanged, with False: the registry keeps what was said first."""
    data = rec.model_dump(mode="json")
    stmt = (
        pg_insert(RecommendationRow)
        .values(
            id=rec.id,
            created_at=rec.created_at,
            league_id=rec.league_id,
            user_id=rec.user_id,
            platform=rec.platform.value,
            format=rec.format.value,
            call_type=rec.call_type.value,
            dedupe_key=dedupe_key,
            claim=data["claim"],
            baseline_call=data["baseline_call"],
            inputs=data["inputs"],
            producer=data["producer"],
            detail=data["detail"],
            expected_value=rec.expected_value,
            confidence=rec.confidence,
            outcome_window_start=rec.outcome_window.start,
            outcome_window_end=rec.outcome_window.end,
            first_tip_utc=rec.first_tip_utc,
            is_shadow=rec.is_shadow,
        )
        .on_conflict_do_nothing(index_elements=["league_id", "dedupe_key"])
        .returning(RecommendationRow.id)
    )
    if session.execute(stmt).scalar() is not None:
        return rec, True
    row = session.scalars(
        select(RecommendationRow).where(
            RecommendationRow.league_id == rec.league_id,
            RecommendationRow.dedupe_key == dedupe_key,
        )
    ).one()
    return to_domain(row), False


def to_domain(row: RecommendationRow) -> Recommendation:
    return Recommendation.model_validate(
        {
            "id": row.id, "created_at": row.created_at, "league_id": row.league_id,
            "user_id": row.user_id, "platform": row.platform, "format": row.format,
            "call_type": row.call_type, "claim": row.claim, "baseline_call": row.baseline_call,
            "inputs": row.inputs, "producer": row.producer, "detail": row.detail,
            "expected_value": row.expected_value, "confidence": row.confidence,
            "outcome_window": {"start": row.outcome_window_start, "end": row.outcome_window_end},
            "first_tip_utc": row.first_tip_utc, "is_shadow": row.is_shadow,
        }
    )  # fmt: skip


def for_league(session: Session, league_id: UUID) -> list[Recommendation]:
    rows = session.scalars(
        select(RecommendationRow)
        .where(RecommendationRow.league_id == league_id)
        .order_by(RecommendationRow.created_at)
    )
    return [to_domain(r) for r in rows]
