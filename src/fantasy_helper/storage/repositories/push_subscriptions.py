"""Web Push subscriptions, one per device (ADR 0029). Every function takes the user: a request
can only ever touch its own user's devices."""

from collections.abc import Collection
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from fantasy_helper.domain.push import PushDevice
from fantasy_helper.storage.tables import PushSubscriptionRow


def upsert(
    session: Session,
    user_id: UUID,
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: str | None,
) -> None:
    """Save this device's subscription. A device that was registered to someone else (the same
    phone, signed in as another user) moves to this user: its endpoint is unique."""
    stmt = pg_insert(PushSubscriptionRow).values(
        id=uuid4(), user_id=user_id, endpoint=endpoint, p256dh=p256dh, auth=auth,
        user_agent=user_agent,
    )  # fmt: skip
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["endpoint"],
            set_={
                "user_id": user_id,
                "p256dh": p256dh,
                "auth": auth,
                "user_agent": user_agent,
            },
        )
    )


def delete_for_user(session: Session, user_id: UUID, endpoint: str) -> int:
    """Remove this user's subscription for that endpoint. Another user's is never touched."""
    result = session.execute(
        delete(PushSubscriptionRow)
        .where(PushSubscriptionRow.user_id == user_id, PushSubscriptionRow.endpoint == endpoint)
        .returning(PushSubscriptionRow.id)
    )
    return len(result.all())


def endpoints_for_user(session: Session, user_id: UUID) -> list[str]:
    return list(
        session.scalars(
            select(PushSubscriptionRow.endpoint)
            .where(PushSubscriptionRow.user_id == user_id)
            .order_by(PushSubscriptionRow.created_at)
        )
    )


def devices_for_user(session: Session, user_id: UUID) -> list[PushDevice]:
    """Everything needed to notify this user's devices, oldest first (the report jobs, ADR 0030)."""
    rows = session.scalars(
        select(PushSubscriptionRow)
        .where(PushSubscriptionRow.user_id == user_id)
        .order_by(PushSubscriptionRow.created_at, PushSubscriptionRow.id)
    )
    return [
        PushDevice(id=row.id, endpoint=row.endpoint, p256dh=row.p256dh, auth=row.auth)
        for row in rows
    ]


def delete_by_ids(session: Session, user_id: UUID, ids: Collection[UUID]) -> int:
    """Remove devices a push service said are gone (404 or 410). Scoped to the user, like every
    function here."""
    if not ids:
        return 0
    result = session.execute(
        delete(PushSubscriptionRow)
        .where(PushSubscriptionRow.user_id == user_id, PushSubscriptionRow.id.in_(ids))
        .returning(PushSubscriptionRow.id)
    )
    return len(result.all())


def mark_success(session: Session, user_id: UUID, ids: Collection[UUID], at: datetime) -> None:
    """Record that a push service accepted a message for these devices."""
    if not ids:
        return
    session.execute(
        update(PushSubscriptionRow)
        .where(PushSubscriptionRow.user_id == user_id, PushSubscriptionRow.id.in_(ids))
        .values(last_success_at=at)
    )
