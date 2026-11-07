"""The GitHub workflows: pinned actions, an alert for every failure the app didn't report, and
Dependabot PRs that merge only after CI passes (ADR 0024)."""

import re
from pathlib import Path

from fantasy_helper.cli._common import REPORTED_EXIT

GITHUB = Path(__file__).parents[2] / ".github"
WORKFLOWS = GITHUB / "workflows"
AUTO_MERGE = WORKFLOWS / "dependabot-auto-merge.yml"


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


def _ci_job_names() -> set[str]:
    return set(re.findall(r"^    name: (.+)$", (WORKFLOWS / "ci.yml").read_text(), re.MULTILINE))


def test_auto_merge_waits_for_every_ci_job() -> None:
    """The guard requires each CI job by its exact name: a renamed job would otherwise drop out of
    main's required checks, and auto-merge could merge without it."""
    text = AUTO_MERGE.read_text()
    guarded = re.search(r"\[(\"[^\]]+)\] - \$required", text)
    assert guarded, "the guard's list of required checks moved"
    assert set(re.findall(r'"([^"]+)"', guarded.group(1))) == _ci_job_names()
    assert 'gh pr merge --auto --squash "$PR_URL"' in text  # GitHub merges once checks pass


def test_auto_merge_runs_only_for_dependabot_with_no_third_party_code() -> None:
    text = AUTO_MERGE.read_text()
    assert re.search(r"^on: pull_request$", text, re.MULTILINE)  # never pull_request_target
    assert "github.event.pull_request.user.login == 'dependabot[bot]'" in text
    assert "github.event.pull_request.head.repo.full_name == github.repository" in text
    assert "\npermissions: {}\n" in text  # write access is granted to the one job only
    assert "uses:" not in text  # this job can write to the repo


def test_dependabot_covers_python_and_actions_after_a_cooldown() -> None:
    text = (GITHUB / "dependabot.yml").read_text()
    assert re.findall(r"package-ecosystem: (\S+)", text) == ["uv", "github-actions"]
    assert text.count("default-days: 7") == 2
    assert text.count("applies-to: security-updates") == 2
    # Python majors are skipped as version updates only; a security fix of any size still comes.
    assert 'update-types: ["version-update:semver-major"]' in text
