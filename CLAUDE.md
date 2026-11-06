# Fantasy Helper: working guide

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

## Architecture contracts (enforced by `lint-imports`)

- Layers, top to bottom: `cli` → `api | mcp_server` → `pipeline` → `evaluation` → `brain | reports | delivery` → `facts | registry` → `engine | identity` → `storage | adapters` → `observability` → `config` → `domain`. A module imports only from layers below it.
- `engine` is pure: no `storage`, `adapters`, `identity`, `observability`, `sqlalchemy` or `httpx`.
- `brain` sees only FactSheets and MCP tools: no `storage`, `adapters`, `identity`, `sqlalchemy` or `httpx`.
- A new top-level package must be added to the contracts in `pyproject.toml`.

## Conventions

- **Time:** every datetime is timezone-aware UTC (`domain._base.UtcDatetime`; Ruff's DTZ rules ban naive datetimes). Convert to the user's zone only for display.
- **Domain models** subclass `DomainModel`: frozen, unknown fields rejected. Invariants live in validators (for example, the tip-off guard on `Recommendation`).
- **New tables** enable row-level security in their migration (`ALTER TABLE ... ENABLE ROW LEVEL SECURITY`, no policies). `tests/integration` fails otherwise.
- **Migrations** are numbered by hand (`--rev-id 0002`). CI runs `alembic check`, so models and migrations can't drift.
- **Structured LLM output** always uses `method="json_schema"`, never forced tool calls (ADR 0015).
- **Logging** uses `observability.logs.get_logger`, never `print`. Keys that name a secret are redacted automatically.
- **Ops alerts** go through `observability.alerts` and never contain secrets or league data.
- **Tests** never read the real `.env` (an autouse fixture isolates them). DB tests only run against a database whose name contains `test`.
- **Dates in docs** are absolute (2026-11-02), never relative.
