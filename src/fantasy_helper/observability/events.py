"""Run events: what every run tells the operator's phone (ADR 0011, docs/runbooks/run-events.md).

A run reports `started` → `step`… → (`retrying`…) → `succeeded` | `warned` | `failed`. Started,
steps and successes are quiet (low priority: no sound, they sit in the notification list) and can
be switched off with FH_NOTIFY_PROGRESS=false. Retries and warnings make a normal sound; failures
are loud. Those are always sent.

These are ops events only: runs, steps, failures. Anything for the user to act on (report ready,
lock reminders) is product push from the phone app (Web Push, M3), never ntfy. Messages carry
counts and statuses, never secrets or league data: anyone who knows the topic can read it.
Every event is also logged, and sending can never break a run.
"""

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from fantasy_helper.config import Settings
from fantasy_helper.domain.ports import Alert, AlertPriority, Clock, Notifier, SystemClock
from fantasy_helper.domain.runs import RunKind
from fantasy_helper.observability.alerts import notifier_from_settings
from fantasy_helper.observability.logs import get_logger

log = get_logger(__name__)

MAX_PROBLEMS_SHOWN = 8


class RunEvent(StrEnum):
    STARTED = "started"
    STEP = "step"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    WARNED = "warned"
    FAILED = "failed"


# Quiet events need FH_NOTIFY_PROGRESS; the others always go out.
_QUIET = frozenset({RunEvent.STARTED, RunEvent.STEP, RunEvent.SUCCEEDED})
_TAGS = {
    RunEvent.STARTED: "arrow_forward",
    RunEvent.STEP: "small_blue_diamond",
    RunEvent.RETRYING: "repeat",
    RunEvent.SUCCEEDED: "white_check_mark",
    RunEvent.WARNED: "warning",
    RunEvent.FAILED: "x",
}


class RunEvents:
    """One run's events. Create it when the run starts, call `started()` first."""

    def __init__(
        self,
        kind: RunKind,
        notifier: Notifier,
        *,
        label: str | None = None,
        progress: bool = True,
        clock: Clock | None = None,
    ) -> None:
        self.kind = kind
        self.label = label or kind.value.title()  # "Ingest", "Daily"…
        self._notifier = notifier
        self._progress = progress
        self._clock = clock or SystemClock()
        self._started_at: datetime | None = None
        self.last_step: str | None = None

    # ------------------------------------------------------------------ the events

    def started(self, detail: str = "") -> None:
        self._started_at = self._clock.now()
        self._emit(RunEvent.STARTED, f"{self.label} started", detail or "Running.")

    def step(self, name: str, detail: str = "") -> None:
        """A step finished. `name` is a fixed phrase ("Box scores loaded"); `detail` is counts."""
        self.last_step = name
        self._emit(RunEvent.STEP, f"{self.label}: {name}", detail or "Done.")

    def retrying(
        self, attempt: int, of: int, wait_seconds: float, detail: str, problems: Sequence[str] = ()
    ) -> None:
        """Some parts failed in a way a retry can fix; they run again after a wait."""
        minutes = wait_seconds / 60
        when = f"Trying again in {minutes:.0f} min." if minutes >= 1 else "Trying again now."
        self._emit(
            RunEvent.RETRYING,
            f"{self.label}: retrying (attempt {attempt} of {of})",
            self._lines(detail, problems, when),
        )

    def succeeded(self, detail: str = "") -> None:
        self._emit(RunEvent.SUCCEEDED, f"{self.label} succeeded", self._with_elapsed(detail))

    def warned(self, problems: Sequence[str], detail: str = "") -> None:
        """Finished, but something needs a look (normal sound)."""
        self._emit(
            RunEvent.WARNED,
            f"{self.label} finished with warnings",
            self._with_elapsed(self._lines(detail, problems)),
        )

    def failed(
        self,
        detail: str,
        problems: Sequence[str] = (),
        *,
        title: str | None = None,
        urgent: bool = False,
    ) -> None:
        """Failed or incomplete (loud). Says which step finished last, so you know where."""
        where = f"Last step done: {self.last_step}." if self.last_step else ""
        self._emit(
            RunEvent.FAILED,
            title or f"{self.label} failed",
            self._with_elapsed(self._lines(detail, problems, where)),
            priority=AlertPriority.URGENT if urgent else AlertPriority.HIGH,
        )

    # ------------------------------------------------------------------ plumbing

    def _with_elapsed(self, text: str) -> str:
        if self._started_at is None:
            return text or "Done."
        seconds = (self._clock.now() - self._started_at).total_seconds()
        took = f"Took {seconds:.0f} s." if seconds < 120 else f"Took {seconds / 60:.1f} min."
        return f"{text}\n{took}" if text else took

    @staticmethod
    def _lines(detail: str, problems: Sequence[str], tail: str = "") -> str:
        shown = [f"• {p}" for p in problems[:MAX_PROBLEMS_SHOWN]]
        if len(problems) > MAX_PROBLEMS_SHOWN:
            shown.append(f"• …and {len(problems) - MAX_PROBLEMS_SHOWN} more (see the run log)")
        return "\n".join(part for part in (detail, *shown, tail) if part)

    def _emit(
        self,
        event: RunEvent,
        title: str,
        message: str,
        priority: AlertPriority | None = None,
    ) -> None:
        log.info("run_event", kind=self.kind.value, run_event=event.value, title=title)
        if event in _QUIET and not self._progress:
            return
        if priority is None:
            priority = AlertPriority.LOW if event in _QUIET else AlertPriority.DEFAULT
        try:
            self._notifier.send(
                Alert(
                    title=title[:120],
                    message=message[:2000],
                    priority=priority,
                    tags=(_TAGS[event],),
                )
            )
        except Exception as exc:  # a notification must never break a run
            log.error("run_event_send_failed", run_event=event.value, error=type(exc).__name__)


def events_from_settings(
    settings: Settings, kind: RunKind, *, label: str | None = None
) -> RunEvents:
    return RunEvents(
        kind, notifier_from_settings(settings), label=label, progress=settings.notify_progress
    )
