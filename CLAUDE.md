# Fantasy Helper: working guide

**Starting a new session? Read [docs/session-handoff.md](docs/session-handoff.md) first.** It has the current state, how we work with Adam, what we learned the hard way, and exactly what's next.

A fantasy basketball assistant: a daily report and weekly plan with specific moves, tuned to each league's scoring, built on the user's own takes, and graded on every call it makes. Read `docs/architecture.md` (system design), `docs/roadmap.md` (milestones and current status) and `docs/decisions/` (ADRs) before changing anything structural.

## Commands

```bash
uv sync                                   # install
uv run pytest                             # tests (DB tests need FH_TEST_DATABASE_URL)
uv run ruff check . && uv run ruff format .
uv run mypy                               # strict
uv run lint-imports                       # architecture contracts
uv run alembic upgrade head               # migrate DATABASE_URL
uv run alembic revision --autogenerate --rev-id 0002 -m "..."   # new migration
uv run fh doctor                          # check config, encryption, DB, tracing, alerts
uv run fh snapshot                        # save today's raw public data (launchd runs it twice a day)
uv run fh ingest                          # load leagues + snapshots into the database (idempotent)
uv run fh run daily [--dry-run] [--open]  # ingest, lock-or-pass calls (logged), daily report page
uv run fh run weekly [--open]             # ingest, weekly overview per league (FactSheets, calls)
uv run fh fixtures record                 # re-record redacted fixtures (then update contract test counts)
scripts/snapshot-schedule.sh status       # snapshot schedule state and logs
```

## Rules that are never broken

1. **Code computes every number; models never produce a stat.** The engine computes values and candidate moves. Models read FactSheets and MCP tools, and guardrails reject drafts with ungrounded numbers (ADR 0004).
2. **Every recommendation goes through the registry, before tip-off, and is never edited** (ADR 0005). Grades go in a separate table.
3. **Every model call is traced in LangSmith.**
4. **Record real API responses once and test against them** before wiring a new source into the pipeline. Fixtures come only from the redacting recorder (ADR 0012).
5. **Secrets live in environment variables and never in git.** User and league data, including league IDs and ESPN cookies, live in the database, and credentials are encrypted (ADR 0007).
6. **Keep the Claude API key out of the shell used to run Claude Code.** The app reads `FH_ANTHROPIC_API_KEY` from `.env` and never `ANTHROPIC_API_KEY`.
7. **One ADR per architecture choice** in `docs/decisions/`: what, why, what was rejected.
8. **Daily snapshots are never skipped or rewritten.** A missed day can't be recovered (ADR 0014).
9. **One box score per player and game, for every league** (ADR 0022). Leagues differ only in their rules, read from each league's own settings. Never assume scoring from a platform, and never score from platform-reported points (those are only checks).

## Architecture contracts (enforced by `lint-imports`)

- Layers, top to bottom: `cli` → `api | mcp_server` → `pipeline` → `evaluation` → `brain | reports | delivery` → `facts | registry` → `engine | identity` → `storage | adapters` → `observability` → `config` → `domain`. A module imports only from layers below it.
- `engine` is pure: no `storage`, `adapters`, `identity`, `observability`, `sqlalchemy` or `httpx`.
- `brain` sees only FactSheets and MCP tools: no `storage`, `adapters`, `identity`, `sqlalchemy` or `httpx`.
- A new top-level package must be added to the contracts in `pyproject.toml`.

## Conventions

- **Time:** every datetime is timezone-aware UTC (`domain._base.UtcDatetime`; Ruff's DTZ rules ban naive datetimes). Convert to the user's zone only for display.
- **Domain models** subclass `DomainModel`: frozen, unknown fields rejected. Invariants live in validators (for example, the tip-off guard on `Recommendation`).
- **New tables** enable row-level security in their migration (`ALTER TABLE ... ENABLE ROW LEVEL SECURITY`, no policies). `tests/integration` fails otherwise. Season-scoped tables carry a season (directly or through their league), so a season can be archived to R2 and removed (ADR 0021). Store stats as typed columns, not JSONB.
- **Hot reads** (agents, MCP tools, the API) come from pre-computed read models (`fact_sheets`, `league_player_values`) with one indexed query per tool call. History tables are for batch jobs (ADR 0020).
- **Migrations** are numbered by hand (`--rev-id 0002`). CI runs `alembic check`, so models and migrations can't drift.
- **Structured LLM output** always uses `method="json_schema"`, never forced tool calls (ADR 0015).
- **Logging** uses `observability.logs.get_logger`, never `print`. Keys that name a secret are redacted automatically.
- **Run events** (ops notifications to the phone) go through `observability.events.RunEvents`: started → steps → succeeded | warned | failed ([docs/runbooks/run-events.md](docs/runbooks/run-events.md)). They're about runs only, never secrets or league data. Anything the user acts on is product push from the phone app.
- **Tests** never read the real `.env` (an autouse fixture isolates them). DB tests only run against a database whose name contains `test`.
- **Dates in docs** are absolute (2026-11-02), never relative.
- **Adapters** return platform records keyed by the platform's own player IDs (`domain.records`); only ingest resolves them to canonical players through `identity`.
- **Player identity** is matched by name and team (ADR 0018). When ingest reports an unlinked rostered player, add a link to `identity/overrides.py`.
