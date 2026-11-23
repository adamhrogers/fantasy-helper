# Dependency updates (Dependabot)

**Written:** 2026-11-07. **Design:** ADR 0024. **Your part:** nothing routine, except that PRs touching the deploy path wait for you to say so (below). The one-time ruleset was created on 2026-11-07.

## What happens on its own

| When | What Dependabot opens | Then |
|---|---|---|
| A security advisory affects a dependency | One grouped PR per ecosystem (Python, Actions), any version | CI runs; it merges itself when CI passes |
| Mondays, 07:00 Eastern | One grouped PR per ecosystem with that week's updates, each release at least 7 days old. No Python major versions | Same |

**Except** PRs that change `.github/workflows/deploy.yml`: those are tested but stay open, because any action in the deploy job can ask for the token of the identity that deploys to prod (ADR 0024, Update 2026-11-23). In practice that's a grouped Actions update that bumps an action the deploy uses. Everything else merges itself: Python, Docker and the phone app's npm updates. A Docker or npm update also waits for its own check (the image check, or the app's `Web` check) to pass before it merges.

Merged PRs show up in `main`'s history as `Bump ...` commits. A PR whose CI fails stays open, and GitHub emails you about it.

## One-time setup: the `main` ruleset (2 min)

Auto-merge waits for CI only if `main` requires CI. Until this ruleset exists, the auto-merge job refuses and Dependabot's PRs stay open.

Either ask Claude to create it (one command, below), or click through it yourself: **Settings → Rules → Rulesets → New ruleset → New branch ruleset**.
- **Ruleset name:** `main`. **Enforcement status:** Active.
- **Bypass list:** add **Repository admin**, so you can push a fix straight to `main` in an emergency.
- **Target branches:** Add target → **Include default branch**.
- **Rules:** keep **Restrict deletions** and **Block force pushes** on. Turn on **Require status checks to pass**, leave **Require branches to be up to date before merging** off, and add both checks: `Lint, types, contracts, tests` and `Secret scan (full history)`.
- **Create.**

The same ruleset as a command:

```bash
gh api repos/adamhrogers/fantasy-helper/rulesets --method POST --input - <<'EOF'
{
  "name": "main",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
  "bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}],
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "required_status_checks", "parameters": {
      "strict_required_status_checks_policy": false,
      "required_status_checks": [
        {"context": "Lint, types, contracts, tests", "integration_id": 15368},
        {"context": "Secret scan (full history)", "integration_id": 15368}
      ]
    }}
  ]
}
EOF
```

`actor_id 5` is the Repository admin role, and `integration_id 15368` is GitHub Actions, so only Actions can report those checks.

Also worth turning on, but optional: **Settings → General → Automatically delete head branches**, so merged branches don't pile up.

## When something needs you

- **A Dependabot PR is open with green checks and hasn't merged.** Either it changes `deploy.yml`, so the auto-merge job leaves it for a person (ADR 0024, Update 2026-11-23), or it's a Docker or npm update whose own check (the image check, or `Web`) failed or took more than 20 minutes, which the "Merge when CI passes" job's log says. Tell Claude "review and merge the held Dependabot PRs": it reads the diff, looks at what the new pin changed upstream, and merges.

- **A Dependabot PR has a red check.** Tell Claude "fix or close the Dependabot PR". Don't merge it by hand with the admin bypass.
- **The auto-merge job fails with "doesn't require these checks".** The ruleset is missing, or a CI job was renamed. Fix the ruleset as above, then open the PR's **Checks** tab and re-run the failed job.
- **A snapshot fails right after a Dependabot merge** (Mondays). Open the merged PR and click **Revert**, merge the revert, then rerun the set: **Actions → Snapshot → Run workflow**.
- **A Python major version** (pydantic 3, SQLAlchemy 3, …): Dependabot doesn't propose these. Claude bumps them on purpose during a milestone.

## Renaming a CI job

The job names are in three places: `.github/workflows/ci.yml`, the ruleset, and the guard in `.github/workflows/dependabot-auto-merge.yml`. Change all three together. `tests/unit/test_workflows.py` fails if the guard and `ci.yml` disagree.
