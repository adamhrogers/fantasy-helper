# Fantasy Helper

A personal fantasy basketball brain that tells you what to do, not just what happened.

Every morning it sends a short briefing to your phone: what happened last night, what moved in your league, and the moves to make today, all built on your league's own scoring rules. Tell it which players you believe in and it watches them for you. It keeps score on every call it makes, so by the end of the season you know exactly how much it helped.

**Status:** M0 (foundation) · see [docs/roadmap.md](docs/roadmap.md)

## How it's built

```
Sources (ESPN, Sleeper, projections) → Adapters → Data model (Postgres) → Engine (code computes)
  → FactSheet → Brain (LangGraph; models argue, guardrails check) → Registry (every call logged
  before tip-off) → Phone app (PWA + push)          Grader → report cards + timeline graphs
```

- **Code computes, models argue.** Every number comes from the engine; models interpret, debate and write.
- **Evidence over opinion.** Every recommendation is logged before the games and graded against a baseline.
- **Any league.** Scoring and rules come from each league's settings; platforms plug in through adapters.

Details: [architecture](docs/architecture.md) · [repository layout](docs/repo-structure.md) · [decision log](docs/decisions/README.md)

## First-time setup

Requires [uv](https://docs.astral.sh/uv/) (`brew install uv`).

```bash
uv sync
uv run pre-commit install
cp .env.example .env
```

Then fill in `.env`. Every value is a system secret or setting, never league data:

1. **Encryption key:** `uv run fh keygen`, pasted into `FH_ENCRYPTION_KEY`.
2. **Database:** create a Supabase project, copy **Connect → Session pooler** into `DATABASE_URL`, then run `uv run alembic upgrade head`.
3. **LangSmith:** create an API key and paste it into `LANGSMITH_API_KEY`.
4. **Ops alerts:** `uv run fh keygen --topic`, pasted into `FH_NTFY_TOPIC`. Subscribe to that topic in the ntfy phone app.

Check everything:

```bash
uv run fh doctor --alert
```

## Development

```bash
uv run pytest                 # DB tests also need FH_TEST_DATABASE_URL (a database named *test*)
uv run ruff check . && uv run ruff format .
uv run mypy
uv run lint-imports           # architecture contracts
```

CI runs all of these plus `alembic check` and a full-history secret scan on every pull request.
