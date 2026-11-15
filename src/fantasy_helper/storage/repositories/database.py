"""Facts about the database itself, and the lock that keeps runs from overlapping."""

from sqlalchemy import text
from sqlalchemy.orm import Session

# One lock for every run that writes (ingest, daily, weekly): "FHRUN" as a number.
PIPELINE_LOCK = 0x4648_5255_4E


def size_bytes(session: Session) -> int:
    """What Supabase counts against the plan's database-size quota (ADR 0021)."""
    return int(session.execute(text("SELECT pg_database_size(current_database())")).scalar_one())


def lock_pipeline(session: Session, *, timeout: str = "10min") -> None:
    """Wait for any other run to finish, then hold the run lock until this transaction ends.

    Cloud Scheduler can start a job twice (at-least-once delivery), and the daily and weekly runs
    can overlap when one is slow. Two runs ingesting at once would fight over the same rows; this
    makes the second wait, then find nothing new to do. A wait longer than `timeout` fails the run
    (retryable), so a stuck run can't hold the others past the job's own time limit.
    """
    # set_config(..., true) is SET LOCAL as a parameterised call: it ends with the transaction.
    session.execute(text("SELECT set_config('lock_timeout', :timeout, true)"), {"timeout": timeout})
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": PIPELINE_LOCK})
