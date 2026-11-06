from sqlalchemy import select
from sqlalchemy.orm import Session

from fantasy_helper.domain.users import Role, User
from fantasy_helper.storage.tables import UserRow


def _to_domain(row: UserRow) -> User:
    return User(
        id=row.id,
        email=row.email,
        display_name=row.display_name,
        timezone=row.timezone,
        role=Role(row.role),
    )


def get_by_email(session: Session, email: str) -> User | None:
    row = session.scalar(select(UserRow).where(UserRow.email == email.strip().lower()))
    return _to_domain(row) if row else None


def get_owner(session: Session) -> User | None:
    row = session.scalar(
        select(UserRow).where(UserRow.role == Role.OWNER.value).order_by(UserRow.created_at)
    )
    return _to_domain(row) if row else None


def create(session: Session, user: User) -> User:
    session.add(
        UserRow(
            id=user.id,
            email=user.email.strip().lower(),
            display_name=user.display_name,
            timezone=user.timezone,
            role=user.role.value,
        )
    )
    session.flush()
    return user
