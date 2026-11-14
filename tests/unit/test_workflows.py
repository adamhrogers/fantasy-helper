"""The GitHub workflows: pinned actions, an alert for every failure the app didn't report,
Dependabot PRs that merge only after CI passes (ADR 0024), and keyless deploys of the scheduled
Cloud Run jobs that agree with the setup script and the settings (ADR 0025)."""

import re
from pathlib import Path

from fantasy_helper.cli._common import REPORTED_EXIT
from fantasy_helper.config import Settings

ROOT = Path(__file__).parents[2]
GITHUB = ROOT / ".github"
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
    assert re.findall(r"package-ecosystem: (\S+)", text) == ["uv", "github-actions", "docker"]
    assert text.count("default-days: 7") == 3
    assert text.count("applies-to: security-updates") == 3
    # Python majors are skipped as version updates only; a security fix of any size still comes.
    assert 'update-types: ["version-update:semver-major"]' in text


DEPLOY = WORKFLOWS / "deploy.yml"
SETUP = ROOT / "scripts" / "gcp-setup.sh"


def _bash_array(script: Path, name: str) -> list[str]:
    match = re.search(rf"^{name}=\(([^)]*)\)", script.read_text(), re.MULTILINE)
    assert match, f"{script.name}: {name}=(...) moved"
    return [quoted or bare for quoted, bare in re.findall(r'"([^"]+)"|(\S+)', match.group(1))]


def test_deploys_are_keyless_and_only_from_main() -> None:
    text = DEPLOY.read_text()
    assert "\npermissions: {}\n" in text
    assert "id-token: write" in text
    assert "contents: read" in text
    assert "environment: prod" in text
    assert "if: github.ref == 'refs/heads/main'" in text
    assert "secrets." not in text  # no key file, no stored credential: Workload Identity only
    assert "persist-credentials: false" in text
    setup = SETUP.read_text()
    assert "assertion.ref == 'refs/heads/main'" in setup
    assert "assertion.repository_id == '$repo_id'" in setup


def test_deployed_secrets_match_the_setup_script_and_the_settings() -> None:
    """A secret named in one place and not the others would leave a job without a setting."""
    mounted = dict(re.findall(r"(FH_\w+)=(fh-[\w-]+):latest", DEPLOY.read_text()))
    aliases = {field.alias for field in Settings.model_fields.values()}
    assert set(mounted) <= aliases
    assert sorted(mounted.values()) == sorted(_bash_array(SETUP, "SECRETS"))
    assert sorted(mounted.values()) == sorted(
        _bash_array(ROOT / "scripts" / "gcp-secrets.sh", "NAMES")
    )


def test_every_scheduled_job_is_deployed_with_its_own_command() -> None:
    deployed = dict(re.findall(r"^\s+deploy (\S+) (\S+)$", DEPLOY.read_text(), re.MULTILINE))
    scheduled = [entry.split(":")[0] for entry in _bash_array(SETUP, "JOBS")]
    assert sorted(deployed) == sorted(scheduled)
    for job, args in deployed.items():
        assert args == "snapshot,--set," + job.removeprefix("snapshot-")
    assert "--max-retries 0" in DEPLOY.read_text()  # a rerun would fetch everything twice


def test_the_container_workflow_holds_nothing_secret() -> None:
    text = (WORKFLOWS / "container.yml").read_text()
    assert re.search(r"^on:\n  pull_request:", text, re.MULTILINE)
    assert "secrets." not in text
    assert "environment:" not in text
    assert "permissions:\n  contents: read\n" in text


def test_the_image_keeps_secrets_and_local_data_out() -> None:
    rules = [
        line
        for line in (ROOT / ".dockerignore").read_text().splitlines()
        if line and not line.startswith("#")
    ]
    assert rules[0] == "*"  # deny everything, then allow only the build inputs
    assert {line for line in rules if line.startswith("!")} == {
        "!pyproject.toml",
        "!uv.lock",
        "!README.md",
        "!src/",
    }
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert re.search(r"^USER fh$", dockerfile, re.MULTILINE)
    for image in re.findall(r"^FROM (\S+)", dockerfile, re.MULTILINE):
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), image
