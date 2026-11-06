"""Timeline graphs: how a take, call or trade looks over time (docs/architecture.md §8.1).

expected_total = realized so far + projected remaining (using that day's projections).
At the end of the window projected_remaining is 0, so the last point equals the final grade.
"""

from datetime import date
from enum import StrEnum
from uuid import UUID

from pydantic import computed_field

from fantasy_helper.domain._base import DomainModel


class SubjectType(StrEnum):
    TAKE = "take"
    CALL = "call"
    TRADE = "trade"


class EvaluationPoint(DomainModel):
    subject_type: SubjectType
    subject_id: UUID
    as_of: date
    realized: float
    projected_remaining: float
    baseline_expected_total: float
    event_ids: tuple[UUID, ...] = ()  # injuries, return-date changes, role changes

    @computed_field  # type: ignore[prop-decorator]
    @property
    def expected_total(self) -> float:
        return self.realized + self.projected_remaining

    @computed_field  # type: ignore[prop-decorator]
    @property
    def lift(self) -> float:
        return self.expected_total - self.baseline_expected_total
