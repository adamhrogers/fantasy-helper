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
    # Does nothing (rather than fail) until gcp-setup.sh has set the variables. The check is a
    # step, not the job's `if`, which runs before the prod environment's variables load.
    assert "vars.GCP_" not in text.split("    steps:\n", 1)[0].split("    env:\n", 1)[0]
    assert 'if [ -z "$WIF_PROVIDER" ] || [ -z "$PROJECT" ]; then' in text
    assert "secrets." not in text  # no key file, no stored credential: Workload Identity only
    assert "persist-credentials: false" in text
    setup = SETUP.read_text()
    assert "assertion.ref == 'refs/heads/main'" in setup
    assert "assertion.repository_id == '$repo_id'" in setup
    # Only this workflow file can deploy, not any workflow on main that asks for an id-token.
    assert "assertion.workflow_ref == '$workflow'" in setup
    assert 'workflow="$GITHUB_REPO/.github/workflows/deploy.yml@refs/heads/main"' in setup


def test_only_mains_newest_commit_deploys_and_daily() -> None:
    """Re-running an old run must not put older code back on the jobs, and Dependabot's merges
    (which start no push workflows) must ship within a day."""
    text = DEPLOY.read_text()
    steps = text.split("    steps:\n", 1)[1]
    after_check = steps.split("id: fresh", 1)[1]
    gated = re.findall(r"^      - (?:name|if|uses): ", after_check, re.MULTILINE)
    assert len(gated) == 3  # auth, build, deploy
    assert after_check.count("if: steps.fresh.outputs.newest == 'true'") == 3
    assert 'cron: "0 15 * * *"' in text


def test_deployed_secrets_match_the_setup_script_and_the_settings() -> None:
    """A secret named in one place and not the others would leave a job without a setting."""
    mounted = dict(re.findall(r"(\w+)=(fh-[\w-]+):latest", DEPLOY.read_text()))
    aliases = {field.alias for field in Settings.model_fields.values()}
    assert set(mounted) <= aliases
    in_setup = set(_bash_array(SETUP, "SNAPSHOT_SECRETS")) | set(
        _bash_array(SETUP, "DATABASE_SECRETS")
    )
    assert set(mounted.values()) == in_setup
    assert set(mounted.values()) == set(_bash_array(ROOT / "scripts" / "gcp-secrets.sh", "NAMES"))


def _jobs_conf() -> list[tuple[str, str, int]]:
    rows = []
    for line in (ROOT / "scripts" / "jobs.conf").read_text().splitlines():
        if line and not line.startswith("#"):
            job, cron, grace = line.split("|")
            rows.append((job, cron, int(grace)))
    return rows


def _deploy_calls() -> dict[str, tuple[str, str, str, str, str]]:
    """job -> (account variable, secrets, args, timeout, memory), from the deploy step."""
    pattern = r'^\s+deploy (\S+) "\$(\w+)" (\S+) (\S+) (\S+) (\S+)$'
    return {
        job: (account, secrets, args, timeout, memory)
        for job, account, secrets, args, timeout, memory in re.findall(
            pattern, DEPLOY.read_text(), re.MULTILINE
        )
    }


EXPECTED_ARGS = {
    "snapshot-morning": "snapshot,--set,morning",
    "snapshot-evening": "snapshot,--set,evening",
    "migrate": "migrate",
    "daily": "run,daily,--require-snapshot-today",
    "weekly": "run,weekly,--require-snapshot-today",
}


def test_every_job_is_deployed_with_its_own_command() -> None:
    deployed = _deploy_calls()
    scheduled = [job for job, _, _ in _jobs_conf()]
    assert sorted(deployed) == sorted([*scheduled, *_bash_array(SETUP, "ONE_OFF_JOBS")])
    assert {job: call[2] for job, call in deployed.items()} == EXPECTED_ARGS
    assert "--max-retries 0" in DEPLOY.read_text()  # a rerun would fetch everything twice


def test_the_database_is_migrated_before_the_jobs_that_need_it_move() -> None:
    """A failed migration must stop the deploy while the report jobs still run the old code
    against the old schema, and the snapshot jobs (no database) must not wait for it."""
    text = DEPLOY.read_text()
    order = [
        text.index(marker)
        for marker in (
            "deploy snapshot-morning",
            "deploy snapshot-evening",
            "deploy migrate",
            "gcloud run jobs execute migrate",
            "deploy daily",
            "deploy weekly",
        )
    ]
    assert order == sorted(order)
    execute = next(ln for ln in text.splitlines() if "jobs execute migrate" in ln)
    assert "--wait" in execute  # without it the deploy would carry on while the migration runs


def test_the_snapshot_jobs_never_get_the_database_or_the_key() -> None:
    """The job that fetches from the public internet can't reach league logins."""
    deployed = _deploy_calls()
    for job, (account, secrets, *_rest) in deployed.items():
        if job.startswith("snapshot-"):
            assert (account, secrets) == ("JOBS_SA", '"$SNAPSHOT_SECRETS"')
        else:
            assert account == "REPORTS_SA", job
    text = DEPLOY.read_text()
    snapshot_secrets = text.split("SNAPSHOT_SECRETS: >-\n", 1)[1].split("\n", 1)[0]
    assert "DATABASE_URL" not in snapshot_secrets
    assert "FH_ENCRYPTION_KEY" not in snapshot_secrets
    setup = SETUP.read_text()
    assert "fh-database-url" not in " ".join(_bash_array(SETUP, "SNAPSHOT_SECRETS"))
    assert 'snapshot-*) echo "$SA_JOBS" ;;' in setup  # the placeholder jobs match the deploy
    # fh-jobs may read the snapshot secrets and only those.
    assert (
        'for secret in "${SNAPSHOT_SECRETS[@]}"; do\n'
        '    quiet gcloud secrets add-iam-policy-binding "$secret" \\\n'
        '      --member "serviceAccount:$SA_JOBS"'
    ) in setup


def test_the_job_table_is_what_cloud_scheduler_and_healthchecks_both_read() -> None:
    rows = _jobs_conf()
    assert [job for job, _, _ in rows] == [
        "snapshot-morning", "snapshot-evening", "daily", "weekly"
    ]  # fmt: skip
    for job, cron, grace in rows:
        assert len(cron.split()) == 5, job
        assert grace >= 60, job
    by_job = {job: (cron, grace) for job, cron, grace in rows}
    # The daily report's check alerts at 07:45, the 8 a.m. promise (run-events runbook).
    assert by_job["daily"] == ("45 6 * * *", 3600)
    setup = SETUP.read_text()
    assert 'done <"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/jobs.conf"' in setup
    assert (
        'CONF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/jobs.conf"'
        in (ROOT / "scripts" / "healthchecks-setup.sh").read_text()
    )


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
