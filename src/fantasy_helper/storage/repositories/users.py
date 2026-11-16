from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
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


def get_by_auth_id(session: Session, auth_user_id: UUID) -> User | None:
    row = session.scalar(select(UserRow).where(UserRow.auth_user_id == auth_user_id))
    return _to_domain(row) if row else None


def link_auth_id(session: Session, auth_user_id: UUID, email: str) -> User | None:
    """Tie a Supabase sign-in to the user with this email, the first time it arrives (ADR 0029).
    Only a user with no sign-in yet can be linked, so a second sign-in with the same email can't
    take over a user who already has one. Returns the user, or None when there's nobody to link."""
    try:
        with session.begin_nested():
            row = session.scalar(
                update(UserRow)
                .where(UserRow.auth_user_id.is_(None), UserRow.email == email.strip().lower())
                .values(auth_user_id=auth_user_id)
                .returning(UserRow)
            )
    except IntegrityError:  # this sign-in id already belongs to someone else
        return None
    return _to_domain(row) if row else None
