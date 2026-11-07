# ADR 0024: Dependencies update themselves: Dependabot PRs that merge once CI passes

**Status:** Accepted · 2026-11-07

## Decision
- **Dependabot watches the Python packages and the GitHub Actions** (`.github/dependabot.yml`). For Python it uses the `uv` ecosystem, which updates `pyproject.toml` and `uv.lock` together.
- **Security fixes** come as soon as an advisory lands, grouped into one PR per ecosystem, whatever the version jump.
- **Version updates** come weekly, Monday 07:00 Eastern, grouped into one PR per ecosystem, and only for releases at least 7 days old (`cooldown`, which security fixes skip). Python major versions aren't proposed. Action majors are.
- **Every Dependabot PR merges itself once CI passes** (`.github/workflows/dependabot-auto-merge.yml`). The job turns on GitHub's auto-merge (squash), and GitHub merges only when `main`'s required checks pass. It never merges a PR whose checks failed.
- **`main` has a ruleset that requires both CI jobs** (tests and secret scan), with an admin bypass so Adam can still push a fix straight to `main` when a snapshot is at stake (rule 8). Without required checks, auto-merge could merge before CI finishes, so the auto-merge job reads the ruleset on every PR and refuses if either check is missing. The PR then stays open: the safe way to fail.

## Why
Adam doesn't want to review dependency PRs (2026-11-07), and security fixes should land in days, not whenever someone gets to them. CI is a strong enough reviewer for routine updates: strict mypy, the import contracts, and 300+ tests that replay recorded ESPN and Sleeper responses and run the R2 store against a fake S3. Those are the code paths the snapshot job uses.

The 7-day cooldown is cheap insurance. Most malicious or broken releases are found and pulled within days, and an app gains nothing from a release on its first day.

Python majors can pass every test and still change behavior (a new default, a deprecation turned error). A major of pydantic or SQLAlchemy is a task for a milestone, not a Monday-morning merge. Action majors are mostly runtime bumps, CI runs the same actions as the snapshot job, and GitHub eventually retires old majors anyway.

Monday 07:00 comes after the morning snapshot (06:17), so a merged update that breaks the snapshot fails at the evening set (17:17), with the day still left to fix it. It doesn't land overnight.

## Rejected
- **Reviewing every PR by hand:** they would pile up, and the security fixes would wait with them.
- **Renovate:** more options, but it's a third-party app with write access to the repo. Dependabot is built into GitHub and already turned on.
- **`dependabot/fetch-metadata` to filter by update type:** another third-party action in a job that can write to the repo. The `ignore` rule in the config already keeps Python majors out.
- **Merging with a personal access token or a GitHub App:** the merge would then start push workflows, but at the price of a long-lived write credential for a public repo.
- **Auto-merging Python majors:** see Why.
- **Requiring branches to be up to date before merging:** auto-merge doesn't update branches, so grouped PRs would stall behind each other. The Python and Actions PRs touch different files, and Dependabot rebases its PRs when they conflict.

## Consequences
- Adam does nothing routinely. A PR whose CI fails stays open; ask Claude to fix it or close it ([runbooks/dependency-updates.md](../runbooks/dependency-updates.md)).
- GitHub doesn't start workflows from events made with its built-in token, so CI doesn't run again on `main` after a Dependabot merge: the PR's run is the gate. Once deploys run on pushes to `main` (M3 part 3), a dependency update ships with the next deploy rather than on its own.
- If a merged update breaks a snapshot, the run alerts the phone as usual. Revert that PR, then rerun the set from Actions.
- A private repo on the free plan can't enforce rulesets. If the repo goes private without GitHub Pro, the guard stops auto-merge and Dependabot's PRs wait for a person (roadmap, "When the repo goes private").
- Renaming a CI job means changing it in three places: `ci.yml`, the ruleset, and the guard. `tests/unit/test_workflows.py` checks that the guard and `ci.yml` agree.
