"""The run log: one row per pipeline run (spec: Observability)."""

from sqlalchemy.orm import Session

from fantasy_helper.domain.runs import RunRecord
from fantasy_helper.storage.tables import RunRow


def record_run(session: Session, run: RunRecord) -> None:
    """Insert a finished run. Callers own the transaction."""
    session.add(
        RunRow(
            id=run.id,
            kind=run.kind.value,
            status=run.status.value,
            started_at=run.started_at,
            ended_at=run.ended_at,
            sources=[s.model_dump(mode="json") for s in run.sources],
            rows_loaded=run.rows_loaded,
            model_cost_usd=run.model_cost_usd,
            errors=list(run.errors),
            shipped_on_time=run.shipped_on_time,
        )
    )
