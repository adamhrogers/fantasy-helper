"""The app: routes, sign-in and one pooled database engine per process (ADR 0020, 0029)."""

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from fantasy_helper import __version__
from fantasy_helper.api import schemas
from fantasy_helper.api.auth import AuthError, TokenVerifier, supabase_verifier
from fantasy_helper.config import Settings
from fantasy_helper.config.settings import origins
from fantasy_helper.domain.reports import ReportKind
from fantasy_helper.domain.users import User
from fantasy_helper.observability.logs import get_logger
from fantasy_helper.storage.db import make_engine
from fantasy_helper.storage.repositories import leagues, push_subscriptions, reports, users

log = get_logger(__name__)
bearer = HTTPBearer(auto_error=False)
router = APIRouter()


def get_session(request: Request) -> Iterator[Session]:
    """One transaction per request: committed when the route returns, rolled back when it raises."""
    with Session(request.app.state.engine) as session, session.begin():
        yield session


def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    """The signed-in user, or 401 (no valid token) or 403 (a valid sign-in that isn't one of ours).
    The API never creates a user: they exist before they can sign in."""
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "sign in again", headers={"WWW-Authenticate": "Bearer"}
    )
    if credentials is None:
        raise unauthorized
    verifier: TokenVerifier | None = request.app.state.verifier
    if verifier is None:  # not configured: refuse everything rather than trust anything
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "sign-in isn't configured")
    try:
        claims = verifier.verify(credentials.credentials)
    except AuthError as exc:
        log.info("sign_in_refused", reason=str(exc))
        raise unauthorized from exc
    user = users.get_by_auth_id(session, claims.subject)
    if user is None and claims.email is not None:
        user = users.link_auth_id(session, claims.subject, claims.email)
    if user is None:
        log.warning("sign_in_not_a_user")  # never the email: it's someone's address
        raise HTTPException(status.HTTP_403_FORBIDDEN, "this sign-in isn't set up for this app")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
DbSession = Annotated[Session, Depends(get_session)]


@router.get("/livez", response_model=schemas.Health, tags=["ops"])
def livez() -> schemas.Health:
    return schemas.Health(status="ok", version=__version__)


@router.get("/v1/me", response_model=schemas.Me, tags=["app"])
def me(user: CurrentUser, session: DbSession) -> schemas.Me:
    return schemas.Me(
        name=user.display_name,
        timezone=user.timezone,
        leagues=[
            schemas.LeagueOut(id=lg.id, name=lg.name, platform=lg.platform.value)
            for lg in leagues.list_for_user(session, user.id)
        ],
    )


@router.get("/v1/reports/{kind}", response_model=schemas.ReportsOut, tags=["app"])
def get_reports(
    kind: Literal["daily", "weekly"],
    user: CurrentUser,
    session: DbSession,
    on: Annotated[date | None, Query(alias="date")] = None,
) -> schemas.ReportsOut:
    """The latest reports of this kind (or those of one date), one per league: one query. Daily
    reports come with that date's games (how many, and when the first tips), from the same query."""
    if kind == "daily":
        found, games = reports.with_game_day(session, user.id, ReportKind.DAILY, on)
    else:
        found, games = reports.for_user(session, user.id, ReportKind.WEEKLY, on), None
    return schemas.ReportsOut(
        for_date=found[0].for_date if found else None, reports=found, games=games
    )


@router.get("/v1/push/public-key", response_model=schemas.PushKey, tags=["push"])
def push_public_key(request: Request, user: CurrentUser) -> schemas.PushKey:
    key = request.app.state.vapid_public_key
    if key is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "notifications aren't set up yet")
    return schemas.PushKey(public_key=key)


@router.put("/v1/push/subscriptions", response_model=schemas.SubscriptionOut, tags=["push"])
def save_subscription(
    body: schemas.SubscriptionIn, request: Request, user: CurrentUser, session: DbSession
) -> schemas.SubscriptionOut:
    push_subscriptions.upsert(
        session,
        user.id,
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
        user_agent=(request.headers.get("user-agent") or "")[:300] or None,
    )
    return schemas.SubscriptionOut(endpoint=body.endpoint)


@router.delete("/v1/push/subscriptions", status_code=status.HTTP_204_NO_CONTENT, tags=["push"])
def remove_subscription(
    body: schemas.SubscriptionRemove, user: CurrentUser, session: DbSession
) -> None:
    push_subscriptions.delete_for_user(session, user.id, body.endpoint)


def create_app(
    settings: Settings,
    *,
    verifier: TokenVerifier | None = None,
    engine_factory: Callable[[], Engine] | None = None,
) -> FastAPI:
    """The app. `verifier` and `engine_factory` are for tests; in production both come from the
    settings: the Supabase project to trust, and the database URL."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if engine_factory is not None:
            engine = engine_factory()
        elif settings.database_url is not None:
            # A small pool: the service runs at most 2 instances, and Supabase's pooler has a
            # connection limit shared with the jobs.
            engine = make_engine(
                settings.database_url.get_secret_value(),
                pool_size=2,
                max_overflow=1,
                pool_recycle=300,
            )
        else:
            raise RuntimeError("DATABASE_URL is not set")
        app.state.engine = engine
        yield
        engine.dispose()

    app = FastAPI(
        title="Fantasy Helper API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,  # nothing about the API is browsable from outside; the schema is committed
        redoc_url=None,
        openapi_url=None,
    )
    app.state.verifier = verifier or (
        supabase_verifier(settings.supabase_url) if settings.supabase_url else None
    )
    app.state.vapid_public_key = settings.vapid_public_key
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins(settings),
        allow_methods=["GET", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,  # tokens travel in a header, never a cookie
        max_age=600,
    )
    app.include_router(router)
    return app
