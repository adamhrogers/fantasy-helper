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


def test_the_snapshot_workflow_is_a_manual_fallback_with_no_schedule() -> None:
    """Cloud Scheduler is the only timer (ADR 0025): a second one saves the same day twice,
    hours late, and doubles the projection snapshots in the database."""
    text = (WORKFLOWS / "snapshot.yml").read_text()
    assert re.search(r"^\s*schedule:", text, re.MULTILINE) is None
    assert "workflow_dispatch:" in text


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


def test_dependabot_covers_python_actions_docker_and_the_phone_app_after_a_cooldown() -> None:
    text = (GITHUB / "dependabot.yml").read_text()
    ecosystems = re.findall(r"package-ecosystem: (\S+)", text)
    assert ecosystems == ["uv", "github-actions", "docker", "npm"]
    assert text.count("default-days: 7") == 4
    assert text.count("applies-to: security-updates") == 4
    # Majors are skipped as version updates only, for Python and for npm; a security fix of any
    # size still comes.
    assert text.count('update-types: ["version-update:semver-major"]') == 2
    # The phone app's packages come from its own folder.
    assert re.search(r"package-ecosystem: npm\n\s+directory: /web\n", text)


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
    mounted = re.findall(r"(\w+)=(fh-[\w-]+):latest", DEPLOY.read_text())
    aliases = {field.alias for field in Settings.model_fields.values()}
    assert {setting for setting, _ in mounted} <= aliases
    in_setup = (
        set(_bash_array(SETUP, "SNAPSHOT_SECRETS"))
        | set(_bash_array(SETUP, "DATABASE_SECRETS"))
        | set(_bash_array(SETUP, "API_SECRETS"))
        | set(_bash_array(SETUP, "PUSH_SECRETS"))
    )
    assert {secret for _, secret in mounted} == in_setup
    # The prompts in gcp-secrets.sh cover every secret a person types. The two push keys are made
    # by scripts/vapid-setup.sh, never typed or pasted, so they aren't prompted for.
    typed = {secret for _, secret in mounted if not secret.startswith("fh-vapid-")}
    assert typed == set(_bash_array(ROOT / "scripts" / "gcp-secrets.sh", "NAMES"))
    assert {secret for _, secret in mounted} - typed == {
        "fh-vapid-private-key",
        "fh-vapid-public-key",
    }


def _jobs_conf() -> list[tuple[str, str, int, str]]:
    rows = []
    for line in (ROOT / "scripts" / "jobs.conf").read_text().splitlines():
        if line and not line.startswith("#"):
            job, cron, grace, trigger = line.split("|")
            rows.append((job, cron, int(grace), trigger))
    return rows


def _deploy_calls() -> dict[str, tuple[str, str, str, str, str, str]]:
    """job -> (account variable, secrets, args, timeout, memory, next job), from the deploy step."""
    pattern = r'^\s+deploy (\S+) "\$(\w+)" (\S+) (\S+) (\S+) (\S+) (\S+)$'
    return {
        job: (account, secrets, args, timeout, memory, nxt)
        for job, account, secrets, args, timeout, memory, nxt in re.findall(
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
    scheduled = [job for job, *_ in _jobs_conf()]
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


def test_the_api_has_its_own_account_and_only_its_own_secrets() -> None:
    """The API checks sign-ins and reads reports. It must never be able to decrypt a league login
    or reach the raw store, so it gets no encryption key and no R2 secret."""
    text = DEPLOY.read_text()
    api_secrets = text.split("API_SECRETS: >-\n", 1)[1].split("\n", 1)[0]
    assert re.findall(r"(\w+)=", api_secrets) == [
        "DATABASE_URL",
        "FH_SUPABASE_URL",
        "FH_APP_ORIGINS",
        "FH_VAPID_PUBLIC_KEY",  # the public half of the push key: it serves it to the app
    ]
    for forbidden in (
        "ENCRYPTION", "R2", "HEALTHCHECKS", "NTFY", "fh-database-url", "VAPID_PRIVATE",
        "fh-vapid-private-key",
    ):  # fmt: skip
        assert forbidden not in api_secrets
    setup = SETUP.read_text()
    api = _bash_array(SETUP, "API_SECRETS")
    assert api == [
        "fh-api-database-url",
        "fh-supabase-url",
        "fh-app-origins",
        "fh-vapid-public-key",
    ]
    # Only fh-api reads the API's secrets; the report jobs and the snapshot jobs never do.
    for account in ("SA_REPORTS", "SA_JOBS"):
        assert f'--member "serviceAccount:${account}"' in setup
    assert (
        'for secret in "${API_SECRETS[@]}"; do\n'
        '    quiet gcloud secrets add-iam-policy-binding "$secret" \\\n'
        '      --member "serviceAccount:$SA_API"'
    ) in setup
    assert 'for secret in "${SNAPSHOT_SECRETS[@]}" "${DATABASE_SECRETS[@]}"; do' in setup
    assert "fh-api-database-url" not in " ".join(_bash_array(SETUP, "DATABASE_SECRETS"))


def test_only_the_report_jobs_can_read_the_private_push_key_and_the_api_only_the_public_one() -> (
    None
):
    """The key that signs every push can notify every user, so the internet-facing API never holds
    it, and neither do the snapshot jobs, which fetch from the public internet (ADR 0030). The
    report jobs read it, and the app's address, which is the VAPID subject."""
    text = DEPLOY.read_text()
    push = text.split("PUSH_SECRETS: >-\n", 1)[1].split("\n", 1)[0]
    assert re.findall(r"(\w+)=(fh-[\w-]+):latest", push) == [
        ("FH_VAPID_PRIVATE_KEY", "fh-vapid-private-key"),
        ("FH_APP_ORIGINS", "fh-app-origins"),
    ]
    # Only the daily and weekly runs get it: they send the push, after their reports commit.
    assert 'REPORT_SECRETS="$SNAPSHOT_SECRETS,$DATABASE_SECRETS,$PUSH_SECRETS"' in text
    deployed = _deploy_calls()
    for job, (account, secrets, *_rest) in deployed.items():
        has_key = "$REPORT_SECRETS" in secrets
        assert has_key == (job in ("daily", "weekly")), job
        if job in ("daily", "weekly"):
            assert account == "REPORTS_SA", job
    snapshot_secrets = text.split("SNAPSHOT_SECRETS: >-\n", 1)[1].split("\n", 1)[0]
    database_secrets = text.split("DATABASE_SECRETS: >-\n", 1)[1].split("\n", 1)[0]
    for group in (snapshot_secrets, database_secrets):
        assert "VAPID" not in group
        assert "APP_ORIGINS" not in group
    assert "DATABASE_URL=fh-database-url:latest migrate" in text  # the migration gets neither
    # In the setup script: the private key is a push secret read by fh-reports alone.
    setup = SETUP.read_text()
    assert _bash_array(SETUP, "PUSH_SECRETS") == ["fh-vapid-private-key"]
    assert "fh-vapid-private-key" not in _bash_array(SETUP, "API_SECRETS")
    assert "fh-vapid-private-key" not in _bash_array(SETUP, "SNAPSHOT_SECRETS")
    binding = (
        'for secret in "${PUSH_SECRETS[@]}" fh-app-origins; do\n'
        '    quiet gcloud secrets add-iam-policy-binding "$secret" \\\n'
        '      --member "serviceAccount:$SA_REPORTS"'
    )
    assert binding in setup
    # No other loop binds a push secret to anyone.
    assert setup.count("${PUSH_SECRETS[@]}") == 2  # the list of all secrets, and that binding


def test_the_api_moves_after_the_migration_and_nothing_makes_it_public_but_the_owner() -> None:
    """The service must never run ahead of its schema, and public access is the owner's one-time
    grant (the deployer can't change IAM), so the deploy never asks for it."""
    text = DEPLOY.read_text()
    execute = text.index("gcloud run jobs execute migrate")
    assert execute < text.index("gcloud run deploy api") > text.index("deploy weekly")
    api_call = text[text.index("gcloud run deploy api") :]
    assert '--service-account "$API_SA"' in api_call
    assert "--args serve" in api_call
    assert "--min-instances 0 --max-instances 2" in api_call
    assert "allow-unauthenticated" not in text
    assert "--member allUsers --role roles/run.invoker" in SETUP.read_text()


def test_the_job_table_is_what_cloud_scheduler_and_healthchecks_both_read() -> None:
    rows = _jobs_conf()
    assert [job for job, *_ in rows] == [
        "snapshot-morning", "snapshot-evening", "daily", "weekly"
    ]  # fmt: skip
    for job, cron, grace, _trigger in rows:
        assert len(cron.split()) == 5, job
        assert grace >= 60, job
    by_job = {job: (cron, grace) for job, cron, grace, _ in rows}

    def deadline(job: str) -> str:
        """cron + grace: when Healthchecks alerts if the job hasn't reported."""
        minute, hour = by_job[job][0].split()[:2]
        total = int(hour) * 3600 + int(minute) * 60 + by_job[job][1]
        return f"{total // 3600:02d}:{total % 3600 // 60:02d}"

    # The daily report's check alerts at 07:45, the 8 a.m. promise (run-events runbook); the
    # weekly overview's at 08:00. A chained job's cron is the chain's start, not its own run.
    assert deadline("daily") == "07:45"
    assert deadline("weekly") == "08:00"
    assert by_job["daily"][0] == by_job["snapshot-morning"][0]
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


def test_the_morning_chain_is_the_same_in_the_job_table_the_workflow_and_the_setup_script() -> None:
    """jobs.conf says who starts whom, deploy.yml gives each job its FH_NEXT_JOB, and gcp-setup.sh
    lets each job start only that one. They must agree, or a hand-over fails at 06:17."""
    triggers = {job: trigger for job, _c, _g, trigger in _jobs_conf()}
    chain = {job: t.removeprefix("after ") for job, t in triggers.items() if t.startswith("after ")}
    assert chain == {"daily": "snapshot-morning", "weekly": "daily"}
    scheduled = {job for job, t in triggers.items() if t == "scheduler"}
    assert scheduled == {"snapshot-morning", "snapshot-evening"}  # nothing else runs on a clock

    nxt = {job: call[5] for job, call in _deploy_calls().items()}
    downstream = {
        up: job for job, up in chain.items()
    }  # snapshot-morning -> daily, daily -> weekly
    for job, expected in nxt.items():
        assert expected == downstream.get(job, "-"), job
    assert "FH_NEXT_JOB=projects/$PROJECT/locations/$REGION/jobs/$7" in DEPLOY.read_text()

    setup = SETUP.read_text()
    assert '"after "*) CHAINED+=("$job:${trigger#after }") ;;' in setup
    # Each job may start the next one, with the starter's own account and nothing broader.
    assert '--member "serviceAccount:$(account_for "$upstream")" --role roles/run.invoker' in setup
    assert 'gcloud scheduler jobs delete "$job"' in setup  # a chained job has no clock of its own


def test_dependabot_holds_only_deploy_yml_and_waits_for_the_image_and_app_checks() -> None:
    """Only an update to deploy.yml waits for a person: any action in the deploy job can ask for the
    token of the identity that deploys to prod. The Dockerfile and the phone app merge themselves,
    but only after their own checks pass, since main's ruleset doesn't require those
    (ADR 0024, Update 2026-11-23)."""
    text = AUTO_MERGE.read_text()
    assert 'gh pr diff "$PR_URL" --name-only' in text
    assert r"grep -E '^\.github/workflows/deploy\.yml$'" in text
    assert "^web/' || true" not in text  # the phone app is no longer held
    assert 'need+=("Image builds and starts")' in text
    assert 'need+=("Types, lint, tests, build")' in text
    # The names it waits for are the jobs' real names.
    assert "name: Image builds and starts" in (WORKFLOWS / "container.yml").read_text()
    assert "name: Types, lint, tests, build" in (WORKFLOWS / "web.yml").read_text()
    assert "checks: read" in text
    assert "completed:success) break" in text  # anything but a pass leaves it open
    merge = text.split("- name: Merge once the required checks pass", 1)[1]
    assert "if: steps.deploy_path.outputs.hold != 'true'" in merge
    assert "gh pr merge --auto --squash" in merge
    assert "uses:" not in text  # still no third-party code in the job that can write to the repo


def test_the_web_workflow_checks_the_phone_app_and_holds_nothing_secret() -> None:
    """The phone app (web/, ADR 0009): CI proves the generated types match the API's committed
    schema, then typechecks, lints, tests, builds and dry-runs the deploy command Cloudflare runs.
    Cloudflare builds from main itself, so nothing here deploys and nothing here is secret."""
    text = (WORKFLOWS / "web.yml").read_text()
    assert re.search(r"^on:\n  pull_request:\n    paths:\n      - \"web/\*\*\"", text, re.MULTILINE)
    assert "node-version-file: web/.node-version" in text  # the same pin Cloudflare reads
    for step in (
        "npm ci",
        "npm run gen:api",
        "git diff --exit-code -- src/api/schema.d.ts",
        "npm run typecheck",
        "npm run lint",
        "npm test",
        "npm run build",
        "npx wrangler deploy --dry-run",
    ):
        assert step in text, step
    for forbidden in ("secrets.", "id-token", "environment:"):
        assert forbidden not in text, forbidden
    assert "\npermissions:\n  contents: read\n" in text
    # Not a required check, like container.yml: the auto-merge guard's list of what main's ruleset
    # must require names only ci.yml's jobs. (The auto-merge job waits for this check itself on a
    # PR that changes web/.)
    guard = AUTO_MERGE.read_text().split("- name: Require CI on the base branch", 1)[1]
    guard = guard.split("- name:", 1)[0]
    assert "Types, lint, tests, build" not in guard


def test_the_phone_app_is_a_workers_static_assets_site_with_a_pinned_node() -> None:
    """ADR 0009's Update: Workers static assets, not Pages, and a single-page-app fallback so
    /today and /week open the app."""
    web = ROOT / "web"
    wrangler = (web / "wrangler.jsonc").read_text()
    assert '"directory": "./dist"' in wrangler
    assert '"not_found_handling": "single-page-application"' in wrangler
    assert re.fullmatch(r"\d+(\.\d+){0,2}", (web / ".node-version").read_text().strip())
    # Cloudflare's npm 10 can't install the test runner's dependencies: the version is pinned.
    assert re.search(r'"packageManager": "npm@\d+\.\d+\.\d+"', (web / "package.json").read_text())


def test_the_phone_app_names_no_project_or_address() -> None:
    """The repo is public. The app learns its API and its Supabase project from build variables
    set in Cloudflare, and the content-security policy allows them by pattern."""
    app_files = [
        path
        for path in (ROOT / "web" / "src").rglob("*")
        if path.suffix in {".ts", ".tsx"}
        and ".test." not in path.name
        and not path.name.startswith("test-")
        and path.name != "schema.d.ts"
    ]
    assert app_files  # the glob found the app
    for path in app_files:
        text = path.read_text()
        assert "supabase.co" not in text, path
        assert ".run.app" not in text, path
