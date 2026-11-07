"""The GitHub workflows: pinned actions, and an alert for every failure the app didn't report."""

import re
from pathlib import Path

from fantasy_helper.cli._common import REPORTED_EXIT

WORKFLOWS = Path(__file__).parents[2] / ".github" / "workflows"


def test_every_action_is_pinned_to_a_commit() -> None:
    """A moved tag can't swap in new code: jobs that hold prod secrets run only reviewed commits."""
    for path in WORKFLOWS.glob("*.yml"):
        for ref in re.findall(r"uses:\s*(\S+)", path.read_text()):
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), f"{path.name}: {ref}"


def test_the_snapshot_job_alerts_on_any_unreported_failure() -> None:
    text = (WORKFLOWS / "snapshot.yml").read_text()
    assert f'"$code" -ne {REPORTED_EXIT}' in text  # the app's "already alerted" exit code
    assert "if: (failure() || cancelled()) && steps.snapshot.outputs.reported != 'true'" in text
    assert "persist-credentials: false" in text
